# https://www.tensorflow.org/text/tutorials/nmt_with_attention
# https://www.tensorflow.org/tutorials/text/image_captioning
import tensorflow as tf
from tensorflow.keras.models import clone_model
from utils import update_tensor_column


def get_imagenet_model(model_name, input_shape):
    # Pick a model from https://keras.io/api/applications
    base_model = eval('tf.keras.applications.' + model_name)
    return base_model(input_shape=input_shape, weights=None, include_top=False)


class EncoderDecoderModel(tf.keras.Model):
    def __init__(
        self, 
        encoder: tf.keras.Model,
        decoder: tf.keras.Model, 
        data_handler = None, # DataHandler instance
        dec_rnn_name = '', # Use if there is no rnn in the encoder
        name = 'EncoderDecoderModel', 
        **kwargs
    ):
        super(EncoderDecoderModel, self).__init__(name=name, **kwargs)
        self.encoder = encoder
        self.decoder = decoder
        self.data_handler = data_handler
        self.dec_rnn_name = dec_rnn_name


    def get_config(self):
        return {
            'encoder': clone_model(self.encoder), 
            'decoder': clone_model(self.decoder),
            'data_handler': self.data_handler,
            'dec_rnn_name': self.dec_rnn_name
        }
    

    @classmethod
    def from_config(cls, config):
        return cls(**config) # to clone model when using kfold training

    
    @tf.function
    def _compute_loss(self, batch):
        batch_images, batch_tokens = batch
        batch_size = batch_images.shape[0]
        loss = tf.constant(0.0)
        
        dec_input = tf.expand_dims([self.data_handler.start_token] * batch_size, 1) 
        if self.dec_rnn_name: 
            enc_output = self.encoder(batch_images)
            dec_units = self.decoder.get_layer(self.dec_rnn_name).units
            hidden = tf.zeros((batch_size, dec_units), dtype=tf.float32)
        else: enc_output, hidden = self.encoder(batch_images)
            
        for i in range(1, self.data_handler.max_length):
            # Passing the features through the decoder
            y_pred, hidden, _ = self.decoder([dec_input, enc_output, hidden])
            loss += self.loss(batch_tokens[:, i], y_pred) 
            
            # Use teacher forcing
            dec_input = tf.expand_dims(batch_tokens[:, i], 1) 
        
        # Update training display result
        metrics = self._update_metrics(batch)
        return loss, {'loss': loss / self.data_handler.max_length, **metrics}


    @tf.function
    def _update_metrics(self, batch):
        batch_images, batch_tokens = batch
        predictions = self.predict(batch_images) 
        self.compiled_metrics.update_state(batch_tokens, predictions)
        return {m.name: m.result() for m in self.metrics}


    @tf.function
    def train_step(self, batch):
        with tf.GradientTape() as tape:
            loss, display_result = self._compute_loss(batch)

        # Apply an optimization step
        gradients = tape.gradient(loss, self.trainable_variables)
        self.optimizer.apply_gradients(zip(gradients, self.trainable_variables))
        return display_result


    @tf.function
    def test_step(self, batch):
        _, display_result = self._compute_loss(batch)
        return display_result
    

    @tf.function
    def _init_pred_tokens(self, batch_size):
        seq_tokens = tf.fill([batch_size, self.data_handler.max_length], 0)
        seq_tokens = tf.cast(seq_tokens, dtype=tf.int64)
        new_tokens = tf.fill([batch_size, 1], self.data_handler.start_token)
        new_tokens = tf.cast(new_tokens, dtype=tf.int64)

        seq_tokens = update_tensor_column(seq_tokens, new_tokens, 0)
        done = tf.zeros([batch_size, 1], dtype=tf.bool)
        return seq_tokens, new_tokens, done


    @tf.function
    def _update_pred_tokens(self, y_pred, seq_tokens, done, pos_idx):
        # Set the logits for all masked tokens to -inf, so they are never chosen
        y_pred = tf.where(self.data_handler.token_mask, float('-inf'), y_pred)
        new_tokens = tf.argmax(y_pred, axis=-1) 

        # Add batch dimension if it is not present after argmax
        if tf.rank(new_tokens) == 1: new_tokens = tf.expand_dims(new_tokens, axis=1)

        # Once a sequence is done it only produces padding token
        new_tokens = tf.where(done, tf.constant(0, dtype=tf.int64), new_tokens)
        seq_tokens = update_tensor_column(seq_tokens, new_tokens, pos_idx)

        # If a sequence produces an `END_TOKEN`, set it `done` after that
        done = done | (new_tokens == self.data_handler.end_token)
        return seq_tokens, new_tokens, done


    @tf.function
    def predict(self, batch_images, return_attention=False):
        batch_size = batch_images.shape[0]
        seq_tokens, new_tokens, done = self._init_pred_tokens(batch_size)
        attentions = tf.TensorArray(dtype=tf.float32, size=0, dynamic_size=True)

        if self.dec_rnn_name: 
            enc_output = self.encoder(batch_images)
            dec_units = self.decoder.get_layer(self.dec_rnn_name).units
            hidden = tf.zeros((batch_size, dec_units), dtype=tf.float32)
        else: enc_output, hidden = self.encoder(batch_images)

        for i in range(1, self.data_handler.max_length):
            y_pred, hidden, attention_weights = self.decoder([new_tokens, enc_output, hidden])
            attentions = attentions.write(i - 1, attention_weights)
            seq_tokens, new_tokens, done = self._update_pred_tokens(y_pred, seq_tokens, done, i)
            if tf.executing_eagerly() and tf.reduce_all(done): break

        if not return_attention: return seq_tokens
        return seq_tokens, tf.transpose(tf.squeeze(attentions.stack()), [1, 0, 2])