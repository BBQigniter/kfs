#!/usr/bin/env python

# Rough workflow for adding node(s) to cluster
# * go through ALL or selected nodes
# * if ALL nodes - just reset everything and reboot
#   * execute on all nodes "kubeadm reset --force"
#   * on node that stop kubelet and containerd
#   * on node that delete folder /etc/cni/net.d
#   * reboot nodes
# * if master node - extra tasks needed
#   * on init-master drain node that should be deleted
#   * on node that should be removed "kubeadm reset --force"
#   * on node that stop kubelet and containerd
#   * on node that delete folder /etc/cni/net.d
#   * on init-master "kubectl delete node NODE_NAME"
#   * reboot the node that was deleted
#   * on other masters edit /etc/kubernetes/manifest/etcd.yaml find the old-node in the --initial-cluster parameter
#   * restart kubelet on master-nodes
# * if worker node - simple tasks need
#   * on init-master drain node that should be deleted
#   * on node that should be removed "kubeadm reset --force"
#   * on node that stop kubelet and containerd
#   * on node that delete folder /etc/cni/net.d
#   * on init-master "kubectl delete node NODE_NAME"
#   * reboot the node that was deleted


import sys
import os
from lib.kfsTools import read_yaml_file, write_yaml_file, get_init_master, download_file, upload_file, get_node_info
from lib.kubeadmCommands import kubeadm_reset_node
from lib.kubectlCommands import kubectl_drain_node, kubectl_delete_node, kubectl_get_nodes
from lib.systemctlCommands import systemctl_restart_kubelet
from lib.checkCommands import check_node_health, check_cluster_file_syntax
from time import sleep


def reset_node_from_cluster(cluster_file, node_names: list, unattended_flag=False):
    """
    Reads given cluster-file and checks if given nodes can be reset.

    :param cluster_file: file
    :param node_names: list
    :param unattended_flag: bool 
    """
    cluster_files_dir = os.path.dirname(os.path.realpath(__file__))
    
    # read cluster-file
    cluster_values = read_yaml_file(cluster_file)
    check_cluster_file_syntax(cluster_values)

    for node_name in node_names:
        if node_name == "ALL":
            print("Going to reset all nodes! This will destroy cluster")
            if unattended_flag:
                print("Unattended flag set, continuing...")
            else:
                user_input = input("Are you sure you want to continue? (yes/no): ")
                if user_input.lower() in ["yes", "y"]:
                    print("Continuing...")
                else:
                    print("Aborting...")
                    sys.exit(2)
                # endif
            # endif
            
            print("######### BEGIN resetting all nodes of cluster " + cluster_values["cluster"]["name"] + " #########")

            for node in cluster_values["nodes"]:
                print("    ######### BEGIN Node-Reset for " + node["nodeName"] + " #########")
                kubeadm_reset_node(node["nodeIp"], node["sshUser"], node["sshKey"])
                print("    ########## END Node-Reset for " + node["nodeName"] + " ##########")
            # endforl
                
            print("########## END resetting all nodes of cluster " + cluster_values["cluster"]["name"] + " ##########")
        else:
            print("Getting data about init-master...")
            node = get_init_master(cluster_values)
            init_master_ip = node["nodeIp"]
            init_master_user = node["sshUser"]
            init_master_key = node["sshKey"]
            
            # check if node is part of cluster
            for cluster_node_name in cluster_values["nodes"]:
                if cluster_node_name["nodeName"] == node_name:
                    if cluster_node_name["type"] == "worker":
                        # * if worker node - simple tasks need - rough workflow
                        #   * on init-master drain node that should be deleted
                        #   * on node that should be removed "kubeadm reset --force"
                        #   * on node that stop kubelet and containerd
                        #   * on node that delete folder /etc/cni/net.d
                        #   * on init-master "kubectl delete node NODE_NAME"
                        #   * reboot the node that was deleted
                        print("Resetting worker node" + cluster_node_name["nodeName"])
                        # First we drain the node this might take a while
                        print("Draining node " + cluster_node_name["nodeName"] + " this might take a while depending on the amount of workloads...")
                        if kubectl_drain_node(init_master_ip, init_master_user, init_master_key, cluster_node_name["nodeName"]):
                            print("Successfully drained the node " + cluster_node_name["nodeName"] + ". Continuing...")
                        # endif
                        
                        # we now reset the node (which includes stopping kubelet, containerd, all containers and deleting the cni-folder
                        # also the node will be rebooted - TODO: this might be a problem
                        print("    ######### BEGIN Node-Reset for " + cluster_node_name["nodeName"] + " #########")
                        kubeadm_reset_node(cluster_node_name["nodeIp"], cluster_node_name["sshUser"], cluster_node_name["sshKey"])
                        print("    ########## END Node-Reset for " + cluster_node_name["nodeName"] + " ##########")
                        
                        # finally we delete the node from the cluster
                        kubectl_delete_node(init_master_ip, init_master_user, init_master_key, cluster_node_name["nodeName"])
                        
                        # TODO: delete node from cluster-file or comment the needed lines
                    elif "init-master" in cluster_node_name["type"]:
                        # * if init-master - abort and tell user to set init-master to different node before continuing, else this will be tricky
                        print("Resetting 'init-master' not supported, please change cluster-config first, define a different master node as init-master and retry!")
                        sys.exit(1)
                    elif cluster_node_name["type"] == "master" or cluster_node_name["type"] == "master+worker":
                        # * if master node - extra tasks needed - rough workflow
                        #   * on init-master drain node that should be deleted
                        #   * on node that should be removed "kubeadm reset --force"
                        #   * on node that stop kubelet and containerd
                        #   * on node that delete folder /etc/cni/net.d
                        #   * on init-master "kubectl delete node NODE_NAME"
                        #   * reboot the node that was deleted
                        #   * on other masters edit /etc/kubernetes/manifest/etcd.yaml find the old-node in the --initial-cluster parameter
                        #   * restart kubelet on master-nodes
                        #   * wait for healthy nodes
                        print("Resetting master node" + cluster_node_name["nodeName"])
                        # First we drain the node this might take a while
                        print("Draining node " + cluster_node_name["nodeName"] + " this might take a while depending on the amount of workloads...")
                        if kubectl_drain_node(init_master_ip, init_master_user, init_master_key, cluster_node_name["nodeName"]):
                            print("Successfully drained the node " + cluster_node_name["nodeName"] + ". Continuing...")
                        # endif

                        # we now reset the node (which includes stopping kubelet, containerd, all containers and deleting the cni-folder
                        # also the node will be rebooted - TODO: this might be a problem
                        print("    ######### BEGIN Node-Reset for " + cluster_node_name["nodeName"] + " #########")
                        kubeadm_reset_node(cluster_node_name["nodeIp"], cluster_node_name["sshUser"], cluster_node_name["sshKey"])
                        print("    ########## END Node-Reset for " + cluster_node_name["nodeName"] + " ##########")

                        # finally we delete the node from the cluster
                        kubectl_delete_node(init_master_ip, init_master_user, init_master_key, cluster_node_name["nodeName"])
                        
                        # we now have to update /etc/kubernetes/manifest/etcd.yaml on other master nodes and restart their kubelet-service and
                        # wait for getting the node back as healthy and ready
                        # we loop through rest of available master nodes
                        # * we download the file
                        # * we edit the file
                        #   * we have to find the line starting with `- --initial-cluster=` and create a string like: `node-1.home.arpa=https://192.168.100.71:2380,node-2.home.arpa=https://192.168.100.72:2380,node-3.home.arpa=https://192.168.100.73:2380`
                        #     an entry is created from `NODENAME=https://NODEIP:2380`, multiple hosts are concatenated with a simple comma
                        # * we upload the file
                        # * we restart the kubelet
                        
                        print("Acquiring current list of control-plane nodes...")
                        current_master_nodes = kubectl_get_nodes(init_master_ip, init_master_user, init_master_key, selector="node-role.kubernetes.io/control-plane")
                        
                        print("Downloading etcd-manifest from remaining master-nodes and preparing new --initial-cluster parameter...")
                        initial_cluster_parameter_list = []
                        for master_node in current_master_nodes:
                            master_node_info = get_node_info(cluster_values, master_node)
                            initial_cluster_parameter_list.append(master_node_info["nodeName"] + "=https://" + master_node_info["nodeIp"] + ":2380")

                            config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + master_node_info["nodeName"]
                            check_config_path = os.path.exists(config_path)

                            if not check_config_path:
                                os.makedirs(config_path)
                            # endif
                            
                            download_file(master_node_info["nodeIp"], master_node_info["sshUser"], master_node_info["sshKey"], remote_source_file="/etc/kubernetes/manifests/etcd.yaml", local_destination_file=config_path + "/etcd.yaml")

                        initial_cluster_parameter_string = ",".join(initial_cluster_parameter_list)
                        print("Future --initial-cluster parameter-value " + initial_cluster_parameter_string)
                        
                        # preparing and uploading etcd-manifests for remaining master-nodes
                        for master_node in current_master_nodes:
                            print("Updating ETCD-config on " + master_node)
                            master_node_info = get_node_info(cluster_values, master_node)

                            config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + master_node_info["nodeName"]
                            etcd_manifest_file = read_yaml_file(config_path + "/etcd.yaml")
                            
                            # TODO: might be a problem in future - this means there only ever must only be 1 container in this manifest!
                            spec_container_commands = etcd_manifest_file["spec"]["containers"][0]["command"]
                            
                            if "etcd" not in spec_container_commands:
                                print("Something went wrong, while trying to update the --initial-cluster parameter in the etcd.yaml manifest, please check!")
                                sys.exit(1)
                            else:
                                # we go through the command-list and only update the proper element
                                updated_spec_container_commands = ["--initial-cluster=" + initial_cluster_parameter_string if "--initial-cluster=" in parameter_string else parameter_string for parameter_string in spec_container_commands]
                            # endif

                            # we replace the previous command-list
                            etcd_manifest_file["spec"]["containers"][0]["command"] = updated_spec_container_commands
                            
                            print("Updating etcd-manifest file...")
                            write_yaml_file(etcd_manifest_file, config_path + "/etcd.yaml")
                            print("Uploading etcd-manifest file...")
                            upload_file(master_node_info["nodeIp"], master_node_info["sshUser"], master_node_info["sshKey"], config_path + "/etcd.yaml", "/etc/kubernetes/manifests/etcd.yaml")
                            
                            print("Restarting kubelet-service on node: " + master_node)
                            systemctl_restart_kubelet(master_node_info["nodeIp"], master_node_info["sshUser"], master_node_info["sshKey"])

                            # check if all ok on init-master and continue
                            while not check_node_health(init_master_ip, init_master_user, init_master_key, master_node_info["nodeName"]):
                                print("Node not ready yet, waiting 5 seconds...")
                                sleep(5)
                            # endwhile
                            
                            print("Node should be OK now...")
                        # endfor
                        
                        # TODO: delete node from cluster-file or comment the needed lines
                    else:
                        print("Something went wrong you should not see this, please check!")
                        sys.exit(2)
                    # endif
                # endif
            # endfor
        # endif
    # endfor
    print("Done!")
# enddef
