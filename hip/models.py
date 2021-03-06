import logging
import tensorflow as tf
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tensorflow.python import debug as tf_debug
from tqdm import tqdm

from hip.utils import TimeSeriesScaler

RANDOM_SEED = 42
# select the past MEMORY_WINDOW values of prediction when 
# calculating the endogenous influence
MEMORY_WINDOW = 7
class TensorHIP():
    """
        Hawkes Intensity Process Model Implemented and Optimized in TensorFlow
        Used for prediction of time series using the Hawkes Self-Excitation 
        model with one or more exogenous sources of influence
    """
    def __init__(self, 
                 xs,
                 ys=None,
                 train_split_size=0.95,
                 l1_param=0,
                 l2_param=0,
                 learning_rate=0.5,
                 num_initializations=5,
                 max_iterations=100,
                 eta_param_mode='random',
                 fix_eta_param_value=None,#0.1,
                 fix_c_param_value=None,#0.5,
                 fix_theta_param_value=None,
                 fix_C_param_value=None,#1.0,
                 scale_series=True,
                 verbose=False,
                 optimizer='l-bfgs',
                 feature_names=None
        ):
        self.num_of_series = len(ys)
        self.x = np.asarray(xs).astype(float)
        # store train-validation-test split points 
        self.train_split_size = train_split_size
                
        if ys is None:
            # assume that xs is also 1d
            self.y = np.zeros(self.x.shape[1]).astype(float)
        else:
            self.y = np.asarray(ys).astype(float)
            
        if len(self.x) > 0:
            self.num_of_exogenous_series = self.x[0].shape[0]            
        else:
            # since we don't have any exogenous info
            # instead of modifying the training module to behave differently
            # define the exogenous data as a vector of zeros with appropriate shape
            self.x = np.asarray([np.zeros_like(self.y)])
            self.num_of_exogenous_series = 0
        self.series_length = self.y[0].shape[0]

        self.num_train = int(self.series_length * train_split_size)
        self.num_cv_train = int(self.num_train * 0.8)
        self.num_cv_test = self.num_train - self.num_cv_train
        self.num_test = self.series_length - self.num_train
        
        self.validation_loss = np.inf

        self.scale_series = scale_series
        if scale_series is True:
            self.series_scaler = TimeSeriesScaler()
            self.x = self.series_scaler.transform_xs(self.x)
            self.ys = self.series_scaler.transform_ys(self.y)
        else:
            self.ys = self.y
        
        # model parameters
        self.model_params = dict()
        self.fixed_c = False
        if fix_c_param_value is not None:
            self.fixed_c = True
            self.model_params['c'] = fix_c_param_value
        self.fixed_theta = False
        if fix_theta_param_value is not None:
            self.fixed_theta = True
            self.model_params['theta'] = (float)(fix_theta_param_value)
        self.fixed_C = False
        if fix_C_param_value is not None:
            self.fixed_C = True
            self.model_params['C'] = fix_C_param_value
        self.fixed_eta = False
        if eta_param_mode != "random":
            self.fixed_eta = True
            if eta_param_mode == 'exo_mean':
                self.model_params['eta'] = np.mean(self.x, dtype=np.float32)
            elif eta_param_mode == 'target_mean':
                self.model_params['eta'] = np.mean(self.ys, dtype=np.float32)
            elif eta_param_mode == 'constant':
                self.model_params['eta'] = fix_eta_param_value
            else:
                self.print_log("Invalid eta initialization mode. reverting to random.")
                self.fixed_eta = False
                
        self.l1_param = l1_param
        self.l2_param = l2_param
        self.learning_rate = learning_rate
        self.max_iterations = max_iterations
        self.num_initializations = num_initializations

        self.verbose = verbose
        if verbose is True:
            logging.basicConfig(level=logging.INFO)

        self.feature_names = feature_names
        self.optimizer = optimizer

    def print_log(self, msg):    
        logging.info(msg)

    def time_decay_base(self, i):
        """
            Kernel Base for the time-decaying exponential kernel
            Increasing per each time step in the series

            Parameters
            ----------
            i
                time series length
        """
        return tf.cast(tf.range(i+1, 1, -1), tf.float32)
    # TODO: NOTE: IF we set range to end at 1, the endogenous effect becomes exponential since we are always adding the last effect
    # To ways to mitigate. add an offset var c, which given the nonconvexicity of the optimization task, makes training harder
    # or 

    def predict(self, x, model_params=None):
        """
            Predict the future values of X series given the previous values in
            the series and a list of influential series.

            Parameters
            ----------
            x
                a list of the previous values of the relative sources of influence.
            mode_params
                 model parameters.
        """
        if model_params is None:
            model_params = self.model_params

        predictions = tf.Variable([])
        i = tf.constant(0)
        train_size = tf.shape(x)[1]
        bias = model_params['eta']
        def loop_body(i, x, pred_history):
            exogenous = tf.reduce_sum(tf.multiply(model_params['mu'], x[:, i]))
            
            endo_history_window_start = tf.maximum(0, i - MEMORY_WINDOW)
            endo_history = pred_history[endo_history_window_start:]
            endogenous = model_params['C'] * tf.reduce_sum(endo_history *
                                                           tf.pow(self.time_decay_base(i - endo_history_window_start) + tf.constant(0.01),#, model_params['c']), 
                                                                tf.tile([-1 - model_params['theta']], [i - endo_history_window_start]))
                                                        )
            tf.Print(endo_history_window_start, [endo_history_window_start])
            new_prediction = tf.add_n([bias
                                       , exogenous
                                       , endogenous]) 
            pred_history = tf.concat([pred_history, [new_prediction]], axis=0)
            i = tf.add(i, 1)
            return [i, x, pred_history]

        loop_condition = lambda i, x, pred_history: tf.less(i, train_size)

        _, _, predictions = tf.while_loop(
                                          cond=loop_condition, 
                                          body=loop_body,
                                          loop_vars=[i, x, predictions], 
                                          shape_invariants=[i.get_shape(), x.get_shape(), tf.TensorShape(None)]
                                         )
        return predictions
            
    def train(self):
        """
            Fit the best HIP model using multiple random restarts by
            minimizing the loss value of the model 
        """ 
        best_validation_loss = self.validation_loss       
        best_model_params = None
        for i in range(self.num_initializations):
            self.print_log("== Initialization " + str(i + 1))
            loss_value, model_params = self._fit(iteration_number=i)
            if loss_value < best_validation_loss or best_model_params == None:
                best_validation_loss = loss_value
                best_model_params = model_params
        self.validation_loss = best_validation_loss
        self.model_params = best_model_params
        
    def _fit(self, iteration_number):
        """
            Internal method for fitting the model at each iteration of the
            training process
        """
        tf.reset_default_graph()
        x_observed = tf.placeholder(tf.float32, name='x_observed')
        y_truth = tf.placeholder(tf.float32, name='y_truth')

        # create params dictionary for easier management
        params_keys = ['eta', 'mu', 'theta', 'C', 'c']
        params = self._init_tf_model_variables(random_seed=RANDOM_SEED + iteration_number)
        eta = params['eta']
        mu = params['mu']
        theta = params['theta']
        C = params['C']
        c = params['c']
        pred = self.predict(x_observed, params)
        loss = (
            tf.sqrt(tf.reduce_sum(tf.square(y_truth - pred))) + 
            self.l1_param * (tf.reduce_sum(tf.abs(mu))) + 
            self.l2_param * (tf.reduce_sum(tf.square(mu)))
        ) 
        previous_loss = np.inf
        optimizer = tf.contrib.opt.ScipyOptimizerInterface(
                                                            loss, 
                                                            method='L-BFGS-B',
                                                            options={'maxiter': self.max_iterations}
                                                        )            
        
        validation_loss_sum = 0 
        self.losses = []
        with tf.Session() as sess:
            tf.set_random_seed(RANDOM_SEED)
            sess.run(tf.global_variables_initializer())
            
            params_vals = sess.run([eta, mu, theta, C, c])
            fitted_model_params = dict(zip(params_keys, params_vals)) 
            xs = self.x 
            ys = self.ys            
            for i in range(self.num_of_series):
                self.print_log("--- Fitting target series #{}".format(i + 1))
                x = xs[i]
                y = ys[i]
                test_split = int(len(y) * self.train_split_size)
                validation_split = int(test_split * self.train_split_size)
                train_x, train_y = x[:, :self.num_cv_train], y[:self.num_cv_train]
                validation_x, validation_y = x[:, self.num_cv_train:self.num_train], y[self.num_cv_train:self.num_train]
                print(fitted_model_params)
                new_predictions = sess.run(
                                        pred, 
                                        feed_dict={
                                            x_observed: train_x
                                        }
                                    )
                print(new_predictions)
                
                optimizer.minimize(session=sess,
                                   feed_dict={
                                        x_observed: train_x,
                                        y_truth: train_y
                                    }
                )

                validation_loss = sess.run(
                                            loss,
                                            feed_dict={
                                                        x_observed: validation_x,
                                                        y_truth: validation_y
                                                    }
                                        ) 
                validation_loss_sum += validation_loss / self.num_of_series
                
            params_vals = sess.run([eta, mu, theta, C, c])
            fitted_model_params = dict(zip(params_keys, params_vals)) 
            
        return validation_loss_sum, fitted_model_params

    def _init_tf_model_variables(self, random_seed=RANDOM_SEED):
        tf.set_random_seed(random_seed)
        if 'mu' in self.model_params:
            mu = tf.get_variable('mu', initializer=tf.constant(self.model_params['mu']))        
        else:
            mu = tf.get_variable(
                name='mu',
                shape=(1, self.num_of_exogenous_series),
                initializer=tf.random_normal_initializer(mean=1, stddev=1, seed=random_seed)
            )
            
        if 'eta' in self.model_params:
            if self.fixed_eta is True:
                eta = tf.constant(self.model_params['eta'])
            else:
                eta = tf.get_variable('eta', initializer=tf.constant(self.model_params['eta']))                                               
        else:
            eta = tf.get_variable(
                name='eta',
                shape=(),
                initializer=tf.random_normal_initializer(mean=0, stddev=0.5),
            )

        if 'theta' in self.model_params:
            if self.fixed_theta is True:
                theta = tf.constant(self.model_params['theta'])
            else:
                theta = tf.get_variable('theta', initializer=tf.constant(self.model_params['theta']))  
        else:
            theta = tf.get_variable(
                name='theta',
                shape=(),
                initializer=tf.random_normal_initializer(mean=10, stddev=5),
                constraint=lambda x: tf.clip_by_value(x, 0.5, np.infty),
            )  
            
        if 'C' in self.model_params:
            if self.fixed_C is True:
                C = tf.constant(self.model_params['C'])
            else:
                C = tf.get_variable('C', initializer=tf.constant(self.model_params['C']))
        else:
            C = tf.get_variable(
                name='C',
                shape=(),
                initializer=tf.random_normal_initializer(mean=3, stddev=1),
                constraint=lambda x: tf.clip_by_value(x, 0.01, np.infty),
            )
        
        if 'c' in self.model_params:
            if self.fixed_c is True:
                c = tf.constant(self.model_params['c'])
            else:
                c = tf.get_variable('c', initializer=tf.constant(self.model_params['c']))
        else:
            c = tf.get_variable(
                name='c',
                shape=(),
                initializer=tf.random_normal_initializer(mean=1, stddev=1),
                constraint=lambda x: tf.clip_by_value(x, 0, np.infty),
            )

        return {
            'eta': eta,
            'mu': mu, 
            'theta': theta, 
            'C': C, 
            'c': c,
        }

    def get_predictions(self):
        # predict future values for the test data
        # Instantiate a new model with the trained parameters
        tf.reset_default_graph()
        x_observed = tf.placeholder(tf.float32, name='x_observed')

        self._init_tf_model_variables()
        
        pred = self.predict(x_observed)
        predictions = []

        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())
            for i in range(self.num_of_series):    
                if self.scale_series is True:
                    x = self.series_scaler.transform_x(self.x[i])
                else:
                    x = self.x[i]
                new_predictions = sess.run(
                                        pred, 
                                        feed_dict={
                                            x_observed: x
                                        }
                                    )
                predictions.append(new_predictions)
        if self.scale_series is True:
            return self.series_scaler.invert_transform_ys(predictions)
        else:
            return predictions
    
    def get_model_parameters(self):
        """
            Getter method to get the model parameters
        """
        return self.model_params.copy()

    def get_validation_rmse(self):
        predictions = self.get_predictions()
        validation_split_start = self.num_cv_train
        validation_split_end = self.num_train

        error = 0
        for i in range(len(predictions)):
            y_truth = self.y[i][validation_split_start:validation_split_end]
            y_pred = predictions[i][validation_split_start:validation_split_end]
            error += np.sum(y_pred - y_truth) ** 2 / len(y_truth)

        return np.sqrt(error / len(predictions))

    def get_test_rmse(self):
        predictions = self.get_predictions()
        test_split_start = self.num_train 

        error = 0
        for i in range(len(predictions)):
            y_truth = self.y[i][test_split_start:]
            y_pred = predictions[i][test_split_start:]
            error += np.sum(y_pred - y_truth) ** 2 / len(y_truth)

        return np.sqrt(error / len(predictions))

    def get_weights_dict(self):
        if self.feature_names != None:
            ret_val = dict(zip(self.feature_names, list(self.model_params['mu'][0])))
        else:
            ret_val = list(self.model_params['mu'][0])

        return ret_val

    def get_params_df(self):
        params_df = pd.DataFrame([{'eta': self.model_params['eta'], 'theta': self.model_params['theta']}])
        mu_df = pd.DataFrame([self.get_weights_dict()], columns=['mu'])
        return pd.concat([params_df, mu_df], axis=1)

    def plot(self, ax=None):
        predictions = self.get_predictions()
        
        num_of_series = len(predictions)
        data_length = len(predictions[0])
        data_test_split_point = self.num_train

        srows = (int)(np.ceil(np.sqrt(num_of_series)))

        display_plot = False

        if ax is None:
            display_plot = True
            fig, axes = plt.subplots(srows, srows, sharex='all')
            fig.set_figheight(10)
            fig.set_figwidth(20)

        for i in range(num_of_series):
            row = (int)(i / srows)
            col = (int)(i % srows)
            truth = self.y[i]

            pred = predictions[i]
            if display_plot:
                if num_of_series == 1:
                    ax = plt
                else:
                    ax = axes[row, col]
            ax.axvline(data_test_split_point, color='k')
            ax.plot(np.arange(data_length - 1), truth[:-1], 'k--', label='Observed #views')

            # plot predictions on training data with a different alpha to make the plot more clear            
            ax.plot(
                        np.arange(data_test_split_point+1),
                        pred[:data_test_split_point+1], 
                        'b-',
                        alpha=0.5,
                        label='Model Fit'
                    )
            ax.plot(
                        np.arange(data_test_split_point, data_length-1),
                        pred[data_test_split_point:-1], 
                        'b-',
                        alpha=1,
                        label='Model Predictions'
                    )
        ax.legend()
        # ax.set_xlabel("Day")
        # ax.set_ylabel("Occurances")
        if display_plot is True:
            plt.show()
            