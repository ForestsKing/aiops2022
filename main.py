'''
Copyright: Copyright (c) 2021 WangXingyu All Rights Reserved.
Description: 
Version: 
Author: WangXingyu
Date: 2022-04-06 20:50:54
LastEditors: WangXingyu
LastEditTime: 2022-04-26 15:04:36
'''
import os
import time
from collections import defaultdict
from re import A

import joblib
import numpy as np
import pandas as pd
import schedule
from sklearn.preprocessing import StandardScaler

from algorithm.anomaly_catboost import CatBoost
from algorithm.micro_rca import PageRCA
from algorithm.spot import SPOT
from utils.data_process.consumer import data_deal, kpi_d, metric_d
from utils.data_process.process_data import (get_raw_data, istio_kpis,
                                             node_kpis, nodes, pod_kpis, pods,
                                             process_data, rca_kpis,
                                             service_kpis, services, upsample)
from utils.submit.submit import submit

is_anomaly = {i: 0 for i in nodes+pods+services}

WAIT_FLAG = False
INIT_FLAG = False

fault_count = 0
fault_timestamp = 0
fault_num = 0

current_check_time = -1


type2id = {
    'k8s容器cpu负载': 0,
    'k8s容器内存负载': 1,
    'k8s容器写io负载': 2,
    'k8s容器网络丢包': 3,
    'k8s容器网络延迟': 4,
    'k8s容器网络资源包损坏': 5,
    'k8s容器网络资源包重复发送': 6,
    'k8s容器读io负载': 7,
    'k8s容器进程中止': 8,
    'node 内存消耗': 9,
    'node 磁盘写IO消耗': 10,
    'node 磁盘空间消耗': 11,
    'node 磁盘读IO消耗': 12,
    'node节点CPU故障': 13,
    'node节点CPU爬升': 14
}

id2type = {
    0: 'k8s容器cpu负载',
    1: 'k8s容器内存负载',
    2: 'k8s容器写io负载',
    3: 'k8s容器网络丢包',
    4: 'k8s容器网络延迟',
    5: 'k8s容器网络资源包损坏',
    6: 'k8s容器网络资源包重复发送',
    7: 'k8s容器读io负载',
    8: 'k8s容器进程中止',
    9: 'node 内存消耗',
    10: 'node 磁盘写IO消耗',
    11: 'node 磁盘空间消耗',
    12: 'node 磁盘读IO消耗',
    13: 'node节点CPU故障',
    14: 'node节点CPU爬升'
}


def main(type='online_test', run_i=0):
    # 训练
    if type == 'train':
        df_node = pd.concat([pd.read_csv('./data/training_data_normal/cloudbed-1/metric/node/kpi_cloudbed1_metric_0319.csv'),
                             pd.read_csv(
                                 './data/training_data_with_faults/tar/2022-03-20-cloudbed1/metric/node/kpi_cloudbed1_metric_0320.csv'),
                             pd.read_csv('./data/training_data_with_faults/tar/2022-03-21-cloudbed1/metric/node/kpi_cloudbed1_metric_0321.csv')])
        df_node.reset_index(drop=True, inplace=True)
        df_node = df_node[df_node['kpi_name'].isin(
            node_kpis)].reset_index(drop=True)
        df_node['value'] = df_node['value'].astype('float')
        print('df_node:\n', df_node)

        df_service = pd.concat([pd.read_csv('./data/training_data_normal/cloudbed-1/metric/service/metric_service.csv'),
                                pd.read_csv(
                                    './data/training_data_with_faults/tar/2022-03-20-cloudbed1/metric/service/metric_service.csv'),
                                pd.read_csv('./data/training_data_with_faults/tar/2022-03-21-cloudbed1/metric/service/metric_service.csv')])
        df_service.reset_index(drop=True, inplace=True)
        print('df_service:\n', df_service)

        dfs_pod = []
        for kpi in pod_kpis:
            df_pod1 = pd.read_csv(
                './data/training_data_normal/cloudbed-1/metric/container/kpi_' + kpi.split('.')[0] + '.csv')
            dfs_pod.append(df_pod1)

            df_pod2 = pd.read_csv(
                './data/training_data_with_faults/tar/2022-03-20-cloudbed1/metric/container/kpi_' + kpi.split('.')[0] + '.csv')
            dfs_pod.append(df_pod2)

            df_pod3 = pd.read_csv(
                './data/training_data_with_faults/tar/2022-03-21-cloudbed1/metric/container/kpi_' + kpi.split('.')[0] + '.csv')
            dfs_pod.append(df_pod3)
        df_pod = pd.concat(dfs_pod, ignore_index=True)
        df_pod.reset_index(drop=True, inplace=True)
        df_pod['kpi_name'] = df_pod['kpi_name'].apply(
            lambda x: x.split('./')[0])
        df_pod = df_pod[df_pod['kpi_name'].isin(
            pod_kpis)].reset_index(drop=True)
        df_pod['cmdb_id'] = df_pod['cmdb_id'].apply(
            lambda x: 'redis-cart' if 'redis-cart' in x else x)
        df_pod = df_pod[df_pod['cmdb_id'] !=
                        'redis-cart'].reset_index(drop=True)
        df_pod['value'] = df_pod['value'].astype('float')
        print('df_pod:\n', df_pod)

    # 离线测试和在线测试
    else:
        print('\n-----------------------------------------------------------\n')
        current_time = int(time.time())
        print('current_timestamp:', current_time)
        print('current_time:', time.strftime(
            '%H:%M:%S', time.localtime(current_time)))
        # 在线测试
        if type == 'online_test':
            global WAIT_FLAG
            global INIT_FLAG
            if len(kpi_d) >= 10 and len(metric_d) > 0:
                kpi_time = next(reversed(kpi_d))
                metric_time = next(reversed(metric_d))

                global current_check_time
                newest_time = min(int(kpi_time), int(metric_time))

                # 一天的开始
                if newest_time % (24*60*60) == 57600:
                    INIT_FLAG = True

                if newest_time != current_check_time and not INIT_FLAG:
                    current_check_time = newest_time
                    print('current_check_timestamp:', current_check_time)
                    print('current_check_time:', time.strftime(
                        '%H:%M:%S', time.localtime(current_check_time)))

                    kpi_list = kpi_d.get(current_check_time, [])
                    df_kpi = pd.DataFrame(
                        kpi_list, columns=['timestamp', 'cmdb_id', 'kpi_name', 'value'])
                    print('df_kpi:\n', df_kpi)

                    kpi_10min_list = []
                    for i in reversed(range(10)):
                        kpi_10min_list += kpi_d.get(
                            current_check_time - i*60, [])
                    df_kpi_10min = pd.DataFrame(
                        kpi_10min_list, columns=['timestamp', 'cmdb_id', 'kpi_name', 'value'])
                    print('df_kpi_10min:\n', df_kpi_10min)

                    metric_list = metric_d.get(current_check_time, [])
                    df_service = pd.DataFrame(metric_list, columns=[
                        'service', 'timestamp', 'rr', 'sr', 'count', 'mrt'])
                    print('df_service:\n', df_service)
                else:
                    df_kpi = pd.DataFrame()
                    df_kpi_10min = pd.DataFrame()
                    df_service = pd.DataFrame()
            else:
                current_check_time = -1
                df_kpi = pd.DataFrame()
                df_kpi_10min = pd.DataFrame()
                df_service = pd.DataFrame()
        # 离线测试
        else:
            current_check_time = 1647788966 - 1647788966 % 60
            current_check_time += run_i*60
            print('current_check_timestamp:', current_check_time)
            print('current_check_time:', time.strftime(
                '%H:%M:%S', time.localtime(current_check_time)))
            df_service = pd.concat([pd.read_csv('./data/training_data_with_faults/tar/2022-03-20-cloudbed1/metric/service/metric_service.csv'),
                                    pd.read_csv('./data/training_data_with_faults/tar/2022-03-21-cloudbed1/metric/service/metric_service.csv')])
            df_service = df_service[df_service['timestamp']
                                    == current_check_time]
            df_service.reset_index(drop=True, inplace=True)
            print('df_service:\n', df_service)

            df_node = pd.concat([pd.read_csv('./data/training_data_with_faults/tar/2022-03-20-cloudbed1/metric/node/kpi_cloudbed1_metric_0320.csv'),
                                 pd.read_csv('./data/training_data_with_faults/tar/2022-03-21-cloudbed1/metric/node/kpi_cloudbed1_metric_0321.csv')])
            df_node.reset_index(drop=True, inplace=True)

            dfs_pod = []
            for kpi in pod_kpis:
                df_pod1 = pd.read_csv(
                    './data/training_data_with_faults/tar/2022-03-20-cloudbed1/metric/container/kpi_' + kpi.split('.')[0] + '.csv')
                dfs_pod.append(df_pod1)

                df_pod2 = pd.read_csv(
                    './data/training_data_with_faults/tar/2022-03-21-cloudbed1/metric/container/kpi_' + kpi.split('.')[0] + '.csv')
                dfs_pod.append(df_pod2)
            dfs_pod.append(pd.read_csv(
                './data/training_data_with_faults/tar/2022-03-20-cloudbed1/metric/istio/kpi_istio_request_duration_milliseconds.csv'))
            dfs_pod.append(pd.read_csv(
                './data/training_data_with_faults/tar/2022-03-21-cloudbed1/metric/istio/kpi_istio_request_duration_milliseconds.csv'))
            # 无用kpi模拟
            dfs_pod.append(pd.read_csv(
                './data/training_data_with_faults/tar/2022-03-20-cloudbed1/metric/istio/kpi_istio_agent_go_goroutines.csv'))
            dfs_pod.append(pd.read_csv(
                './data/training_data_with_faults/tar/2022-03-21-cloudbed1/metric/istio/kpi_istio_agent_go_goroutines.csv'))
            df_pod = pd.concat(dfs_pod, ignore_index=True)
            df_kpi = pd.concat([df_node, df_pod], ignore_index=True)

            df_kpi_10min = df_kpi[(df_kpi['timestamp'] >= current_check_time-9*60) & (
                df_kpi['timestamp'] <= current_check_time)].reset_index(drop=True)
            print('df_kpi_10min:\n', df_kpi_10min)

            df_kpi = df_kpi[df_kpi['timestamp'] ==
                            current_check_time].reset_index(drop=True)
            print('df_kpi:\n', df_kpi)
        # 离线测试和在线测试
        if not (df_kpi.empty or df_kpi_10min.empty):
            df_kpi['kpi_name'] = df_kpi['kpi_name'].apply(
                lambda x: x.split('./')[0])
            df_kpi['value'] = df_kpi['value'].astype('float')

            df_node = df_kpi[df_kpi['kpi_name'].isin(
                node_kpis)].reset_index(drop=True)
            print('df_node:\n', df_node)

            df_pod = df_kpi[df_kpi['kpi_name'].isin(
                pod_kpis)].reset_index(drop=True)
            df_pod['cmdb_id'] = df_pod['cmdb_id'].apply(
                lambda x: 'redis-cart' if 'redis-cart' in x else x)
            df_pod = df_pod[df_pod['cmdb_id'] !=
                            'redis-cart'].reset_index(drop=True)
            print('df_pod:\n', df_pod)

            df_kpi_10min['value'] = df_kpi_10min['value'].astype('float')

            df_rca = df_kpi_10min[df_kpi_10min['kpi_name'].isin(
                rca_kpis)].reset_index(drop=True)
            print('df_rca:\n', df_rca)

            df_kpi_10min['kpi_name'] = df_kpi_10min['kpi_name'].apply(
                lambda x: 'istio_request_duration_milliseconds' if 'istio_request_duration_milliseconds' in x else x)
            df_istio = df_kpi_10min[df_kpi_10min['kpi_name'].isin(
                istio_kpis)].reset_index(drop=True)
            print('df_istio:\n', df_istio)

            rca_timestamp = df_rca.drop_duplicates(
                ['timestamp'])['timestamp'].to_list()
            print('rca_timestamp:\n', rca_timestamp)
        else:
            df_node = pd.DataFrame()
            df_pod = pd.DataFrame()
            df_rca = pd.DataFrame()
            df_istio = pd.DataFrame()
            rca_timestamp = []
    # 训练和离线测试和在线测试
    if type == 'train' or not(df_node.empty or df_service.empty or df_pod.empty or len(rca_timestamp) < 10):
        if type == 'train':
            train = True
        else:
            train = False

        df_node = get_raw_data(df_node, type='node', train=train)
        df_service = get_raw_data(df_service, type='service', train=train)
        df_pod = get_raw_data(df_pod, type='pod', train=train)

        df = pd.concat([df_node, df_service, df_pod], axis=1)
        print('df:\n', df)

        cmdb = nodes + services + pods
        node_pod_kpis = node_kpis+pod_kpis
        df_anomaly, df_cat = process_data(df, cmdb, node_pod_kpis, type=type)
        print('df_anomaly:\n', df_anomaly)
        print('df_cat:\n', df_cat)

        spot = SPOT(1e-3)
        anomaly_catboost = CatBoost()
        # 训练
        if train:
            spot.train(df_anomaly)

            df_cat = df_cat.reset_index()
            df_cat['timestamp'] = pd.to_datetime(
                df_cat['timestamp'], unit='s')

            label = pd.concat([pd.read_csv('./data/training_data_with_faults/groundtruth/groundtruth-k8s-1-2022-03-20.csv'),
                               pd.read_csv(
                './data/training_data_with_faults/groundtruth/groundtruth-k8s-1-2022-03-21.csv')])
            label['failure_type'] = label['failure_type'].apply(
                lambda x: type2id[x])
            label['timestamp'] = (pd.to_datetime(
                label['timestamp'], unit='s') + pd.to_timedelta('30s')).round('min')
            label = label[['timestamp', 'failure_type']]

            cat_data = pd.merge(label, df_cat, on='timestamp', how='left')
            cat_data = pd.concat([cat_data, upsample(cat_data)])
            cat_data = cat_data.sample(frac=1.0).reset_index(drop=True)
            cat_data_x = cat_data.iloc[:, 2:]
            cat_data_y = cat_data['failure_type']

            anomaly_catboost.train(cat_data_x.values, cat_data_y.values)
        # 离线测试和在线测试
        else:
            res = spot.detect(df_anomaly)

            for idx, abn in enumerate(res):
                if abn == True:
                    is_anomaly[cmdb[idx]] += 1
                else:
                    is_anomaly[cmdb[idx]] = 0

            fault_flag, anomaly_dict = spot.check_anomaly(is_anomaly)
            print(fault_flag)
            print(anomaly_dict)
            # 有异常
            if fault_flag:
                global fault_count
                fault_count += 1
                global fault_num
                fault_num += 1
                global fault_timestamp
                if fault_num == 1:
                    fault_timestamp = current_check_time
                if fault_count >= 2 and current_check_time-fault_timestamp <= 60*3:
                    fault_count = 0
                    anomaly_count = len(
                        anomaly_dict['service'])+len(anomaly_dict['pod'])+len(anomaly_dict['node'])
                    # 只检测到了一个cmdb波动，显然这个就是异常
                    if anomaly_count == 1:
                        for _, l in anomaly_dict.items():
                            if len(l) > 0:
                                cmdb_ans = l[0]
                                break
                    # 检测到多个cmdb波动，再进行rca判断根因是哪个
                    else:
                        rca = PageRCA(ts=current_check_time,
                                      fDict=anomaly_dict, responds=df_istio, metric=df_rca)
                        cmdb_ans = rca.do_rca()
                    print('cmdb_ans:', cmdb_ans)

                    type_ans = anomaly_catboost.test(df_cat.values)
                    type_ans = list(map(lambda x: id2type[x], type_ans))[0]
                    print('type_ans:', type_ans)

                    # 在线测试
                    if type == 'online_test':
                        code = submit([str(cmdb_ans), str(type_ans)])
                        print('return_code:', code)

                    print('current_time:', time.strftime(
                        '%Y-%m-%d %H:%M:%S', time.localtime(time.time())))
                    print('fault_time:', time.strftime(
                        '%Y-%m-%d %H:%M:%S', time.localtime(current_check_time)))
                    print('total_fault_num:', fault_num)

                    WAIT_FLAG = True

                fault_timestamp = current_check_time


def init_scaler():
    while True:
        if len(kpi_d) > 0 and len(metric_d) > 0:
            kpi_time = next(reversed(kpi_d))
            metric_time = next(reversed(metric_d))
            newest_time = min(int(kpi_time), int(metric_time))

            if newest_time % (24*60*60) == 61140:
                kpi_60min_list = []
                for i in reversed(range(60)):
                    kpi_60min_list += kpi_d.get(
                        newest_time - i*60, [])
                df_kpi_60min = pd.DataFrame(
                    kpi_60min_list, columns=['timestamp', 'cmdb_id', 'kpi_name', 'value'])
                print('df_kpi_60min:\n', df_kpi_60min)

                metric_60min_list = []
                for i in reversed(range(60)):
                    metric_60min_list += metric_d.get(
                        newest_time - i*60, [])
                df_service_60min = pd.DataFrame(
                    metric_60min_list, columns=[
                        'service', 'timestamp', 'rr', 'sr', 'count', 'mrt'])
                print('df_metric_60min:\n', df_service_60min)

                df_kpi_60min['kpi_name'] = df_kpi_60min['kpi_name'].apply(
                    lambda x: x.split('./')[0])
                df_kpi_60min['value'] = df_kpi_60min['value'].astype('float')

                df_node_60min = df_kpi_60min[df_kpi_60min['kpi_name'].isin(
                    node_kpis)].reset_index(drop=True)
                print('df_node_60min:\n', df_node_60min)

                df_pod_60min = df_kpi_60min[df_kpi_60min['kpi_name'].isin(
                    pod_kpis)].reset_index(drop=True)
                df_pod_60min['cmdb_id'] = df_pod_60min['cmdb_id'].apply(
                    lambda x: 'redis-cart' if 'redis-cart' in x else x)
                df_pod_60min = df_pod_60min[df_pod_60min['cmdb_id'] !=
                                            'redis-cart'].reset_index(drop=True)
                print('df_pod_60min:\n', df_pod_60min)

                df_node_60min = get_raw_data(
                    df_node_60min, type='node', train=True)
                df_service_60min = get_raw_data(
                    df_service_60min, type='service', train=True)
                df_pod_60min = get_raw_data(
                    df_pod_60min, type='pod', train=True)

                df_60min = pd.concat(
                    [df_node_60min, df_service_60min, df_pod_60min], axis=1)
                print('df_60min:\n', df_60min)

                std = joblib.load('./model/scaler/std.pkl')
                random_nums = []
                for i in range(1207):
                    random_nums.append(np.random.normal(
                        0, 0.01*std[i], size=60))
                random_nums = np.array(random_nums).T
                df_60min.iloc[:, :] = df_60min.values + random_nums
                online_std_scaler = StandardScaler()
                df_60min.iloc[:, :] = np.abs(
                    online_std_scaler.fit_transform(df_60min.values))
                joblib.dump(online_std_scaler,
                            './model/scaler/online_std_scaler.pkl')
                break
            else:
                time.sleep(10)
        else:
            print('wait for 60 minutes for init online std scaler...')
            time.sleep(60*60)


if __name__ == '__main__':
    type = 'online_test'
    print('current type:', type)
    if type == 'train':
        main(type)

    elif type == 'online_test':
        data_deal()
        schedule.every().minute.at(':59').do(main, type)

        while True:
            if INIT_FLAG:
                INIT_FLAG = False
                schedule.clear()
                print('wait for 50 minutes for init online std scaler...')
                time.sleep(60*50)
                print('init online std scaler...')
                init_scaler()
                schedule.every().minute.at(':59').do(main, type)

            if WAIT_FLAG:
                WAIT_FLAG = False
                schedule.clear()
                print('wait for 10 minutes...')
                time.sleep(60*10)
                schedule.every().minute.at(':59').do(main, type)

                fault_count = 0
                for i, _ in is_anomaly.items():
                    is_anomaly[i] = 0

            schedule.run_pending()

    elif type == 'offline_test':
        for i in range(-2, 6):
            main(type, i)

    else:
        print('error type')
