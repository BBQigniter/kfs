#!/usr/bin/env python

# just a simple funtion to check current state/node of a cluster

from lib.kubectlCommands import kubectl_get_nodes_status
from lib.checkCommands import check_cluster_file_syntax
from lib.kfsTools import read_yaml_file, get_init_master


def show_cluster_state(cluster_file, unattended_flag=False):
    """
    Just list the current nodes

    :param cluster_file: file
    :param unattended_flag: bool - not really used here
    :return: bool
    """

    # read cluster-file
    cluster_values = read_yaml_file(cluster_file)
    check_cluster_file_syntax(cluster_values)
    
    print("Getting data about init-master...")
    node = get_init_master(cluster_values)
    init_master_ip = node["nodeIp"]
    init_master_user = node["sshUser"]
    init_master_key = node["sshKey"]

    kubectl_get_nodes_status(init_master_ip, init_master_user, init_master_key)
    
    return True
# enddef

