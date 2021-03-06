import os

from keras import callbacks
from keras import initializers
from keras import backend as K
from keras.models import Model
from keras.layers import (Input, BatchNormalization, Activation, Conv2D, MaxPooling2D,
                          Permute, Dense, LSTM, Lambda, TimeDistributed, Flatten, Bidirectional)
from crnn.utils import DataGenerator
from dataset.utils import train_val_split


def ctc_loss_layer(args):
    """
    y_true: True label.
    y_pred: Predict label.
    pred_length: Predict label length.
    label_length: True label length.

    :param args: (y_true, y_pred, pred_length, label_length).
    :return: batch_cost with shape (batch_size, 1).
    """

    y_true, y_pred, pred_length, label_length = args
    batch_cost = K.ctc_batch_cost(y_true, y_pred, pred_length, label_length)
    return batch_cost


def fake_ctc_loss(y_true, y_pred):
    return y_pred


class CNN_BLSTM_CTC:

    @staticmethod
    def build(img_size, num_classes, max_label_length, is_training=True):

        initializer = initializers.he_normal()
        img_width, img_height = img_size

        def PatternUnits(inputs, index, activation="relu"):
            """
            A pattern unit with both BatchNormalization and Activation.

            :param inputs: The previous inputs.
            :param index: The index that used in name.
            :param activation: Activation method, default is relu.
            :return: The outputs.
            """
            inputs = BatchNormalization(name="BN_%d" % index)(inputs)
            inputs = Activation(activation, name="Relu_%d" % index)(inputs)

            return inputs

        inputs = Input(shape=(img_height, img_width, 1), name='img_inputs')
        x = Conv2D(64, (3, 3), padding="same", kernel_initializer=initializer, name='Conv2d_1')(inputs)
        x = PatternUnits(x, 1)
        x = MaxPooling2D(strides=2, name='Maxpool_1')(x)
        x = Conv2D(128, (3, 3), padding="same", kernel_initializer=initializer, name='Conv2d_2')(x)
        x = PatternUnits(x, 2)
        x = MaxPooling2D(strides=2, name='Maxpool_2')(x)

        x = Conv2D(256, (3, 3), padding="same", kernel_initializer=initializer, name='Conv2d_3')(x)
        x = PatternUnits(x, 3)
        x = Conv2D(256, (3, 3), padding="same", kernel_initializer=initializer, name='Conv2d_4')(x)
        x = PatternUnits(x, 4)
        x = MaxPooling2D(pool_size=(2, 1), strides=(2, 1), name='Maxpool_3')(x)

        x = Conv2D(512, (3, 3), padding="same", kernel_initializer=initializer, name='Conv2d_5')(x)
        x = PatternUnits(x, 5)
        x = Conv2D(512, (3, 3), padding="same", kernel_initializer=initializer, name='Conv2d_6')(x)
        x = PatternUnits(x, 6)
        x = MaxPooling2D(pool_size=(2, 1), strides=(2, 1), name='Maxpool_4')(x)

        x = Conv2D(512, (2, 2), padding='same', activation='relu', kernel_initializer=initializer, name='Conv2d_7')(x)
        x = PatternUnits(x, 7)
        conv_output = MaxPooling2D(pool_size=(2, 1), name="Conv_output")(x)
        x = Permute((2, 3, 1), name='Permute')(conv_output)

        rnn_input = TimeDistributed(Flatten(), name='Flatten_by_time')(x)
        y = Bidirectional(LSTM(256, kernel_initializer=initializer, return_sequences=True),
                          merge_mode='sum', name='LSTM_1')(rnn_input)
        y = BatchNormalization(name='BN_8')(y)
        y = Bidirectional(LSTM(256, kernel_initializer=initializer, return_sequences=True), name='LSTM_2')(y)

        y_pred = Dense(num_classes, activation='softmax', name='y_pred')(y)
        y_true = Input(shape=[max_label_length], name='y_true')
        y_pred_length = Input(shape=[1], name='y_pred_length')
        y_true_length = Input(shape=[1], name='y_true_length')
        ctc_loss_output = Lambda(ctc_loss_layer, output_shape=(1,), name='ctc_loss_output')(
            [y_true, y_pred, y_pred_length, y_true_length])

        base_model = Model(inputs=inputs, outputs=y_pred)
        # base_model.summary()
        model = Model(inputs=[y_true, inputs, y_pred_length, y_true_length], outputs=ctc_loss_output)
        # model.summary()

        if is_training:
            return model
        else:
            return base_model

    @staticmethod
    def train(model, src_dir, save_dir, img_size, batch_size, max_label_length, down_sample_factor, aug_nbr, epochs):
        print("[*] Setting up for checkpoints.")
        ckpt = callbacks.ModelCheckpoint(save_dir + "ep{epoch:03d}-loss{loss:.3f}-val_loss{val_loss:.3f}.h5",
                                         save_weights_only=True, save_best_only=True)
        reduce_lr_cbk = callbacks.ReduceLROnPlateau(patience=3)
        logging = callbacks.TensorBoard(log_dir=save_dir)

        print("[*] Setting up for compiler.")
        model.compile(optimizer='adam', loss={'ctc_loss_output': fake_ctc_loss})
        print("[*] Preparing data generator.")
        train_list, val_list = train_val_split(src_dir)
        train_gen = DataGenerator(train_list,
                                  img_shape=img_size,
                                  down_sample_factor=down_sample_factor,
                                  batch_size=batch_size,
                                  max_label_length=max_label_length,
                                  max_aug_nbr=aug_nbr,
                                  width_shift_range=15,
                                  height_shift_range=10,
                                  zoom_range=12,
                                  shear_range=15,
                                  rotation_range=20,
                                  blur_factor=5,
                                  add_noise_factor=0.01)
        val_gen = DataGenerator(val_list, img_size, down_sample_factor, batch_size, max_label_length)
        print("[*] Training start!")
        model.fit_generator(generator=train_gen.flow(),
                            steps_per_epoch=200,
                            validation_data=val_gen.flow(),
                            validation_steps=val_gen.data_nbr // batch_size,
                            callbacks=[ckpt, reduce_lr_cbk, logging],
                            epochs=epochs)
        print("[*] Training finished!")
        model.save(save_dir + "crnn_model.h5")
        print("[*] Model has been successfully saved in %s!" % save_dir)

        # If do data augment, will generate a temp.npz file
        # Usually it's very large, so clean it and save some space.
        if train_gen.local_dataset_path is not None:
            size = os.path.getsize(train_gen.local_dataset_path)
            size = size / float(1024 ** 2)
            print("[!] The temp data is token %.2f Mb space." % size)
            op = input("   Do you want clean it? [y/n]: ")
            if op == 'y':
                os.remove(train_gen.local_dataset_path)

        return 0
