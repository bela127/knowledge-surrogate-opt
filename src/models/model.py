from abc import ABC, abstractmethod
import tensorflow as tf
from src.metrics import RMSE, MAE, DifferenceObjectiveFunction, ToleranceAccuracy, DropoutHistory
from src.optimizers import Lion, AdamW
import numpy as np
import os

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'


class Model(ABC):
    def __init__(self, name: str, input_dim, output_dim):
        self.name = name
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.model = None
        self.compile_args = None

    @abstractmethod
    def build(self):
        pass

    def compile(self, optimizer: str, loss: str, metrics_inverse: bool = True, tolerance: int = 3,
                loss_metric=None, obj_function=None):
        if self.compile_args is None:
            self.compile_args = (optimizer, loss, metrics_inverse, tolerance, loss_metric, obj_function)

        if self.model is None:
            self.build()

        metrics = [RMSE(inverse=metrics_inverse),
                   RMSE(name='rmse_max', obj_function=lambda x: tf.math.reduce_max(x), inverse=metrics_inverse),
                   RMSE(name='rmse_obj', obj_function=obj_function, inverse=metrics_inverse),
                   DifferenceObjectiveFunction(name='difference_max', obj_function=lambda x: tf.math.reduce_max(x),
                                               inverse=metrics_inverse),
                   DifferenceObjectiveFunction(obj_function=obj_function, inverse=metrics_inverse),
                   ToleranceAccuracy(tolerance=tolerance, inverse=metrics_inverse)]
        if loss_metric is not None:
            metrics.insert(0, loss_metric)
        if not isinstance(loss_metric, MAE):
            metrics.insert(2, MAE(inverse=metrics_inverse))

        self.metrics = metrics

        if optimizer == 'lion':
            optimizer = Lion()
        elif optimizer == 'adamw':
            optimizer = AdamW()

        self.model.compile(optimizer=optimizer, loss=loss, metrics=metrics)

    def reload(self, is_mc_dropout: bool):
        self.is_mc_dropout = is_mc_dropout
        self.build()
        self.model.load_weights(self.best_model_filepath)
        self.compile(*self.compile_args)

    def train(self, train_dataset, val_dataset, epochs: int = 100, verbose: int = 1,
              early_stop_patience: int = 100, best_model_filepath: str = 'tmp/'):
        self.best_model_filepath = best_model_filepath + 'best_weights.h5'
        self.last_epoch_filepath = best_model_filepath + 'last_epoch_weights.h5'
        nan_terminate = tf.keras.callbacks.TerminateOnNaN()
        early_stopping = tf.keras.callbacks.EarlyStopping(monitor='val_rmse_obj', patience=early_stop_patience,
                                                          mode='min', restore_best_weights=True)
        model_checkpoint_best = tf.keras.callbacks.ModelCheckpoint(self.best_model_filepath,
                                                                   monitor='val_rmse_obj', mode='min',
                                                                   save_best_only=True,
                                                                   save_weights_only=True)
        model_checkpoint_last = tf.keras.callbacks.ModelCheckpoint(self.last_epoch_filepath,
                                                                   save_weights_only=True, save_best_only=False)
        callbacks = [nan_terminate, early_stopping, model_checkpoint_best, model_checkpoint_last]
        if any(hasattr(layer, 'p_logit') for layer in self.model.layers):
            callbacks.append(DropoutHistory())

        history = self.model.fit(train_dataset, validation_data=val_dataset, epochs=epochs,
                                 verbose=verbose, callbacks=callbacks)

        self.model.load_weights(self.best_model_filepath)

        return history

    def evaluate(self, dataset, verbose: int = 0):
        scores = self.model.evaluate(dataset, verbose=verbose)
        metrics_values = {}
        for i, metric in enumerate(self.metrics):
            metrics_values[metric.name] = scores[i + 1]

        return metrics_values

    def predict(self, x, verbose: int = 0):
        return self.model.predict(x, verbose=verbose)

    def mc_predict(self, dataset, mc_iterations: int, mean: bool = True):
        is_multi_path = isinstance(dataset.element_spec[0], tuple)
        if is_multi_path:
            x_images_values = []
        x_values = []
        y_values = []
        for x, y in dataset:
            if is_multi_path:
                x_images_values.append(x[0].numpy())
                x_values.append(x[1].numpy())
            else:
                x_values.append(x.numpy())
            y_values.append(y.numpy())

        if is_multi_path:
            x_images_np = np.concatenate(x_images_values, axis=0)
        x_np = np.concatenate(x_values, axis=0)
        y_np = np.concatenate(y_values, axis=0)

        if is_multi_path:
            mc_predictions = np.stack([self.predict((x_images_np, x_np)) for _ in range(mc_iterations)], axis=0)
        else:
            mc_predictions = np.stack([self.predict(x_np) for _ in range(mc_iterations)], axis=0)

        if mean:
            mc_predictions = mc_predictions.mean(axis=0)

        return y_np, mc_predictions

    def mc_evaluate(self, dataset, mc_iterations: int):
        y_true, y_pred = self.mc_predict(dataset, mc_iterations=mc_iterations, mean=True)

        y_true = tf.constant(y_true, dtype=tf.float32)
        y_pred = tf.constant(y_pred, dtype=tf.float32)

        metrics_values = {}
        for metric in self.metrics:
            metric_value = metric(y_true, y_pred).numpy()
            metrics_values[metric.name] = metric_value

        return metrics_values