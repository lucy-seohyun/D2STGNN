from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import argparse
import numpy as np
import os
import pandas as pd
import pickle
import torch
import torch.nn.functional as F

from sklearn.preprocessing import MinMaxScaler, StandardScaler

def generate_graph_seq2seq_io_data(
        df, x_offsets, y_offsets, add_time_in_day=True, add_day_in_week=True
):
    """
    Generate samples from
    :param df:
    :param x_offsets:
    :param y_offsets:
    :param add_time_in_day:
    :param add_day_in_week:
    :param scaler:
    :return:
    # x: (epoch_size, input_length, num_nodes, input_dim)
    # y: (epoch_size, output_length, num_nodes, output_dim)
    """
    add_one_hot = False
    num_time_slot_a_day     = 4 # 6시간 단위 예측
    num_day_a_week          = 7
    print("warning: number of time slot in a day is set to {0}".format(num_time_slot_a_day))
    print(f"df_shape: {df.shape}")
    num_samples, num_nodes = df.shape ## row cnt -> 시간, column cnt -> 노드로 형식 바꿔야함
    data = np.expand_dims(df.values, axis=-1) ## -> 3번째 axis가 channel 개수로
    feature_list = [data]
    if add_time_in_day:
        # numerical time_in_day
        time_ind = (df.index.values - df.index.values.astype("datetime64[D]")) / np.timedelta64(1, "D")
        time_in_day = np.tile(time_ind, [1, num_nodes, 1]).transpose((2, 1, 0))
        feature_list.append(time_in_day)
        if add_one_hot:
            # one_hot_time_in_day
            time_in_day_index = list(range(data.shape[0]))
            time_in_day_one_hot_index = [_%num_time_slot_a_day for _ in time_in_day_index]
            # Considering memory consumption, do not generate one hot encoding here
            # time_in_day_one_hot_numpy = F.one_hot(torch.tensor(time_in_day_one_hot_index), num_classes=288).unsqueeze(1).expand(-1, num_nodes, -1).numpy()
            time_in_day_one_hot_index = torch.tensor(time_in_day_one_hot_index).unsqueeze(1).expand(-1, num_nodes).unsqueeze(-1).numpy()
            feature_list.append(time_in_day_one_hot_index)

    if add_day_in_week:
        # numerical day_in_week
        dow = df.index.dayofweek
        dow_tiled = np.tile(dow, [1, num_nodes, 1]).transpose((2, 1, 0))
        feature_list.append(dow_tiled)
        # one_hot_day_in_week
        if add_one_hot:
            day_in_week_index = list(range(data.shape[0]))
            day_in_week_one_hot_index = [_%num_day_a_week for _ in day_in_week_index]
            # Considering memory consumption, do not generate one hot encoding here
            # day_in_week_one_hot_numpy = F.one_hot(torch.tensor(day_in_week_one_hot_index), num_classes=num_day_a_week).numpy()
            time_in_day_one_hot_index = torch.tensor(day_in_week_one_hot_index).unsqueeze(1).expand(-1, num_nodes).unsqueeze(-1).numpy()
            feature_list.append(time_in_day_one_hot_index)

    data = np.concatenate(feature_list, axis=-1)
    x, y = [], []
    min_t = abs(min(x_offsets))
    max_t = abs(num_samples - abs(max(y_offsets)))  # Exclusive
    for t in range(min_t, max_t):  # t is the index of the last observation.
        x.append(data[t + x_offsets, ...])
        y.append(data[t + y_offsets, ...])
    x = np.stack(x, axis=0)
    y = np.stack(y, axis=0)
    return x, y


def generate_train_val_test(args, scaler=None):
    seq_length_x, seq_length_y = args.seq_length_x, args.seq_length_y
    df = pd.read_hdf(args.traffic_df_filename)
    # 0 is the latest observed sample.
    x_offsets = np.sort(np.concatenate((np.arange(-(seq_length_x - 1), 1, 1),)))
    # Predict the next one hour
    y_offsets = np.sort(np.arange(args.y_start, (seq_length_y + 1), 1))
    # x: (num_samples, input_length, num_nodes, input_dim)
    # y: (num_samples, output_length, num_nodes, output_dim)
    x, y = generate_graph_seq2seq_io_data(
        df,
        x_offsets=x_offsets,
        y_offsets=y_offsets,
        add_time_in_day=True,
        add_day_in_week=args.dow
    )

    print("x shape: ", x.shape, ", y shape: ", y.shape)
    # Write the data into npz file.
    num_samples = x.shape[0]
    num_test = round(num_samples * 0.2)
    num_train = round(num_samples * 0.7)
    num_val = num_samples - num_test - num_train
    x_train, y_train = x[:num_train], y[:num_train]
    x_val, y_val = (
        x[num_train: num_train + num_val],
        y[num_train: num_train + num_val],
    )
    x_test, y_test = x[-num_test:], y[-num_test:]

    if scaler is not None: # 중요: X, y 전부 변환  (fit은 x_train에만)
        scaler.fit(x_train[:, :, :, 0].reshape(-1, 1))
        x_train[:, :, :, 0] = scaler.transform(x_train[:, :, :, 0].reshape(-1, 1)).reshape(x_train.shape[:3])
        y_train[:, :, :, 0] = scaler.transform(y_train[:, :, :, 0].reshape(-1, 1)).reshape(y_train.shape[:3])
        x_val[:, :, :, 0] = scaler.transform(x_val[:, :, :, 0].reshape(-1, 1)).reshape(x_val.shape[:3])
        y_val[:, :, :, 0] = scaler.transform(y_val[:, :, :, 0].reshape(-1, 1)).reshape(y_val.shape[:3])
        x_test[:, :, :, 0] = scaler.transform(x_test[:, :, :, 0].reshape(-1, 1)).reshape(x_test.shape[:3])
        y_test[:, :, :, 0] = scaler.transform(y_test[:, :, :, 0].reshape(-1, 1)).reshape(y_test.shape[:3])
        fd = open(os.path.join(args.output_dir, 'scaler.pkl'), 'wb') # 나중에 모델 evaluate 코드에서 써야함으로 저장
        pickle.dump(scaler, fd)
        fd.close()

    for cat in ["train", "val", "test"]:
        _x, _y = locals()["x_" + cat], locals()["y_" + cat]
        print(cat, "x: ", _x.shape, "y:", _y.shape)
        np.savez_compressed(
            os.path.join(args.output_dir, f"{cat}.npz"),
            x=_x,
            y=_y,
            x_offsets=x_offsets.reshape(list(x_offsets.shape) + [1]),
            y_offsets=y_offsets.reshape(list(y_offsets.shape) + [1]),
        )

if __name__ == '__main__':
    seq_length_x    = 6 # 6 단위 예측 (36시간)
    seq_length_y    = 6
    y_start         = 1
    dow             = True
    dataset         = "CHUNGNAM"
    output_dir  = 'datasets/CHUNGNAM'
    traffic_df_filename = 'datasets/raw_data/CHUNGNAM/chungnam_population.h5'
    
    parser  = argparse.ArgumentParser()
    parser.add_argument("--output_dir", type=str, default=output_dir, help="Output directory.")
    parser.add_argument("--traffic_df_filename", type=str, default=traffic_df_filename, help="Raw traffic readings.",)
    parser.add_argument("--seq_length_x", type=int, default=seq_length_x, help="Sequence Length.",)
    parser.add_argument("--seq_length_y", type=int, default=seq_length_y, help="Sequence Length.",)
    parser.add_argument("--y_start", type=int, default=y_start, help="Y pred start", )
    parser.add_argument("--dow", type=bool, default=dow, help='Add feature day_of_week.')
    
    args    = parser.parse_args()
    if os.path.exists(args.output_dir):
        reply   = str(input(f'{args.output_dir} exists. Do you want to overwrite it? (y/n)')).lower().strip()
        if reply[0] != 'y': exit
    else:
        os.makedirs(args.output_dir)
    generate_train_val_test(args, scaler=MinMaxScaler())
