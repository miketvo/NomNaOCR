import tensorflow as tf
from sklearn.model_selection import KFold


def kfold_decorator(n_splits, random_state=None, is_subclassed_model=False):
    kf = KFold(n_splits=n_splits, random_state=random_state, shuffle=True)
    valid_datasets = []
    best_epochs = []
    edist_logs = []
    histories = []
    models = []

    def decorator(func):
        def wrapper(model, img_paths, labels, *args, **kwargs):
            for fold_idx, (train_idx, valid_idx) in enumerate(kf.split(img_paths, labels)):
                if is_subclassed_model: reset_model = model.__class__.from_config({})
                else: reset_model = tf.keras.models.clone_model(model)
                reset_model._name = f'Model_{fold_idx + 1}'

                print(f'============== Fold {fold_idx + 1:02d} training ==============')
                valid_tf_dataset, best_epoch, edist_log, history, reset_model = func(
                    reset_model, img_paths, labels, train_idx, valid_idx
                )

                valid_datasets.append((valid_tf_dataset, valid_idx))
                best_epochs.append(best_epoch)
                edist_logs.append(edist_log)
                histories.append(history)
                models.append(reset_model)
            return valid_datasets, best_epochs, edist_logs, histories, models
        return wrapper
    return decorator


def get_best_fold(valid_datasets, best_epochs, edist_logs, histories, models):
    best_loss = float('inf')
    for fold_idx, model in enumerate(models):
        final_edist = edist_logs[fold_idx][best_epochs[fold_idx]]
        print(f'Fold {fold_idx + 1:02d} - Mean edit distance: {final_edist}')

        steps = None
        valid_tf_dataset, valid_idx = valid_datasets[fold_idx]
        dataset_size = tf.data.experimental.cardinality(valid_tf_dataset).numpy()

        if dataset_size == tf.data.experimental.INFINITE_CARDINALITY:
            for batch in valid_tf_dataset.take(1): 
                batch_size = batch['image'].shape[0]
            steps = len(valid_idx) // batch_size

        final_val_loss = model.evaluate(valid_tf_dataset, steps=steps, verbose=1)
        if final_val_loss < best_loss:
            best_loss = final_val_loss
            best_fold_idx = fold_idx
            
    return (
        valid_datasets[best_fold_idx], 
        best_epochs[best_fold_idx],
        edist_logs[best_fold_idx], 
        histories[best_fold_idx],
        models[best_fold_idx],
        best_fold_idx, 
        best_loss
    )