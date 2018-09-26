import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt

# stop the optimization process after doing a certain number of iterations
OPTIMIZAITION_MAX_ITERATIONS = 1500
OPTIMIZATION_LOSS_TOLERANCE = 0.001

RANDOM_SEED = 42

class TensorHIP():
    """
        Hawkes Intensity Process Model Implemented and Optimized in TensorFlow
        Used for prediction of time series using the Hawkes Self-Excitation 
        model with one or more exogenous sources of influence

        Parameters
        -----------
        x
            a list of the time-series for possible sources of influence in 
            predicting the target series
        y
            target time series
        num_train

        num_test:
    """
    def __init__(self, x, y, train_split_size=0.8):
        self.x = np.array(x)
        self.y = np.array(y)

        # do the train-validation-test split 
        # (use same split: Train = 0.8 * 0.8 * length, 
        # validation = 0.2 * 0.8 * length, test = 0.2 * length)
        test_split = int(len(y) * train_split_size)
        validation_split = int(test_split * train_split_size)
        self.train_x, self.train_y = self.x[:, :validation_split], self.y[:validation_split]
        self.validation_x, self.validation_y = (
                                                  self.x[:, validation_split:test_split], 
                                                  self.y[validation_split:test_split]
                                               )  
        self.test_x, self.test_y = self.x[:, test_split:], self.y[test_split:]

        # model parameters
        self.gamma = 0
        self.eta = 0
        self.mu = 0
        self.theta = 0
        self.C = 0
                
    def time_decay_base(self, i):
        """
            Kernel Base for the time-decaying exponential kernel
            Increasing per each time step in the series

            Parameters
            ----------
            i
                time series length
        """
        return tf.cast(tf.range(i, 0, -1), tf.float32)

    def predict(self, x_curr, y_hist, model_params=None):
        """
            Predict the future values of X series given the previous values in
            the series and a list of influential series.

            Parameters
            ----------
            x_curr
                 previous values of the series we're trying to predict.
            y_hist
                 a list of the previous values of the relative sources of influence.
            mode_params
                 model parameters.
        """
        if model_params is None:
            model_params = self.model_params

        return (
                  model_params['eta'] + tf.reduce_sum(tf.multiply(model_params['mu'], x_curr))
                  + model_params['C'] * (tf.reduce_sum(y_hist * tf.pow(self.time_decay_base(tf.shape(y_hist)[0]),
                  tf.tile([-1 - model_params['theta']], [tf.shape(y_hist)[0]]))))
               )       
                
    def train(self, num_iterations, op='adagrad', verbose=True):
        """
            Fit the best HIP model using multiple random restarts by
            minimizing the loss value of the model 
            
            Parameters
            ----------
            num_iterations
                number of random restarts for fitting the model
            op 
                choice of the optimzier ('adagrad', 'adam')
            verbose 
                print logs

            Returns
            -------
            best_loss
                best loss value achieved among the iterations
        """
        self.model_params = dict()

        best_loss = np.inf
        for i in range(num_iterations):
            if verbose == True:
                print("== Initialization " + str(i + 1))
            loss_value, model_params = self._fit(iteration_number=i, optimization_algorithm=op)

            if loss_value < best_loss:
                best_loss = loss_value
                self.model_params = model_params

        return best_loss

    def _init_tf_model_variables(self):
        eta = tf.get_variable('eta', initializer=tf.constant(self.model_params['eta']))                        
        mu = tf.get_variable('mu', initializer=tf.constant(self.model_params['mu']))        
        theta = tf.get_variable('theta', initializer=tf.constant(self.model_params['theta']))  
        C = tf.get_variable('C', initializer=tf.constant(self.model_params['C']))
        gamma = tf.get_variable('gamma', initializer=tf.constant(self.model_params['gamma']))

    def get_predictions(self):
        # predict future values for the test data

        # Instantiate a new model with the trained parameters
        tf.reset_default_graph()
        
        x_observed = tf.placeholder(tf.float32, name='x_observed')
        y_history = tf.placeholder(tf.float32, name='y_history')
        y_current = tf.placeholder(tf.float32, name='y')
        self._init_tf_model_variables()
        
        pred = self.predict(x_observed, y_history)
        loss = (tf.square(y_current - pred) / 2)

        with tf.Session() as sess:
            tf.set_random_seed(RANDOM_SEED)
            sess.run(tf.global_variables_initializer())
            predictions = np.zeros_like(self.y)
            losses = np.zeros_like(self.test_y)

            # get loss values on the test data
            for index, y in enumerate(self.test_y):
                losses[index] = sess.run(
                                            loss, 
                                            feed_dict={
                                                x_observed: self.test_x[:, index], 
                                                y_history: self.test_y[:index], 
                                                y_current: y
                                            }
                                        )

            # get model prediction for all of the data
            for index, y in enumerate(self.y):
                predictions[index] = sess.run(pred, 
                                          feed_dict={
                                                    x_observed: self.x[:, index],
                                                    y_history: self.y[:index], 
                                                    y_current: self.y[index]
                                                    }
                                         )
            
        # TODO: What to do when predictions are zero (enforce max(0, pred)?)
        # for i in range(len(predictions)):
        #     if predictions[i] < 0:
        #         predictions[i] = 0
                
        return predictions, losses
   
    def _fit(self, iteration_number, optimization_algorithm='adagrad'):
        """
            Internal method for fitting the model at each iteration of the
            training process
        """
        tf.reset_default_graph()
        x_observed = tf.placeholder(tf.float32, name='x_observed')
        y_history = tf.placeholder(tf.float32, name='y_history')
        y_current = tf.placeholder(tf.float32, name='y')

        # The model: 
        # eta + sum(mu[i], x_observed[i]) + C * (kernel_base ^ -(1 + theta))
        eta = tf.get_variable(
                              name='eta',
                              shape=(),
                              initializer=tf.random_uniform_initializer(0, 30, seed=RANDOM_SEED + iteration_number)
                            )

        mu = tf.get_variable(
                             name='mu',
                             shape=(1, len(self.x)),
                             initializer=tf.random_uniform_initializer(-3, 3, seed=RANDOM_SEED + iteration_number)
                            )        

        theta = tf.get_variable(
                                name='theta',
                                shape=(),
                                initializer=tf.random_uniform_initializer(0, 30, seed=RANDOM_SEED + iteration_number)
                               )  

        C = tf.get_variable(
                            name='C',
                            shape=(),
                            initializer=tf.random_uniform_initializer(0, 30, seed=RANDOM_SEED + iteration_number)
                           )

        gamma = tf.get_variable(
                                name='gamma',
                                shape=(),
                                initializer=tf.random_uniform_initializer(0, 30, seed=RANDOM_SEED + iteration_number)
                               )
        
        # create params dictionary for easier management
        params_keys = ['eta', 'mu', 'theta', 'C', 'gamma']
        params_values = [eta, mu, theta, C, gamma]
        params = dict(zip(params_keys, params_values))

        pred = self.predict(x_observed, y_history, params)
        # TODO: Check effect of adding regularization
        loss = (tf.square(y_current - pred) / 2)
        previous_loss = np.inf
        iteration_counter = 1

        if optimization_algorithm == 'adam':
            optimizer = tf.train.AdamOptimizer(learning_rate=0.1).minimize(loss)
        elif optimization_algorithm == 'adagrad':
            optimizer = tf.train.AdagradOptimizer(learning_rate=0.5).minimize(loss)
        
        with tf.Session() as sess:
            tf.set_random_seed(RANDOM_SEED)
            sess.run(tf.global_variables_initializer())

            while iteration_counter < OPTIMIZAITION_MAX_ITERATIONS:  
                for index, y in enumerate(self.train_y):
                    sess.run(optimizer, 
                            feed_dict={
                                        x_observed: self.train_x[:, index],
                                        y_history: self.y[:index], 
                                        y_current: self.y[index]
                                    }
                            )

                losses = np.zeros_like(self.validation_y)
                for index, y in enumerate(self.validation_y): 
                    losses[index] = sess.run(
                                            loss,
                                            feed_dict={
                                                        x_observed: self.validation_x[:, index],
                                                        y_history: self.validation_y[:index],
                                                        y_current: y
                                                    }
                                            )

                # Check if optimization iteration produces improvements to the loss value
                # higher than a relative tolerance: tol = |prev_loss - curr_loss| / min(prev_loss, curr_loss)
                curr_loss = losses.sum()
                # TODO: Handle possible division by zero
                relative_loss = abs(previous_loss - curr_loss) / min(previous_loss, curr_loss)

                if relative_loss < OPTIMIZATION_LOSS_TOLERANCE: break
                
                previous_loss = losses.sum()
                iteration_counter += 1

            params_vals = sess.run([eta, mu, theta, C, gamma])
            fitted_model_params = dict(zip(params_keys, params_vals)) 
        
        return curr_loss, fitted_model_params
    
    def plot_predictions(self):
        """
            Plot the current predictions from the fitted model 
        """
        predictions, _ = self.get_predictions()
        
        data_length = len(self.y)
        data_test_split_point = len(self.train_y) + len(self.validation_y)
        plt.axvline(data_test_split_point, color='k')

        plt.plot(np.arange(data_length), self.y, 'k--', label='Observed #views')

        colors = iter(plt.cm.rainbow(np.linspace(0, 1, self.x.shape[0])))
        for index, exo_source in enumerate(self.x):
            c = next(colors)
            plt.plot(np.arange(data_length), exo_source, c=c, alpha=0.3, label='exo #{0}'.format(index))

        # plot predictions on training data with a different alpha to make the plot more clear            
        plt.plot(
                    np.arange(data_test_split_point),
                    predictions[:data_test_split_point], 
                    'b-',
                    alpha=0.3,
                    label='Model Fit'
                )
        plt.plot(
                    np.arange(data_test_split_point, data_length),
                    predictions[data_test_split_point:], 
                    'b-',
                    alpha=1,
                    label='Model Predictions'
                )


        plt.legend()        
        plt.xlabel('Time')
        plt.ylabel('Y')
        plt.title("Prediction Vs. Truth")

        plt.show()

    def get_model_parameters(self):
        """
            Getter method to get the model parameters
        """
        return self.model_params