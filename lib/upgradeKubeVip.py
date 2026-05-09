#!/usr/bin/env python

# rough workflow
#
# * change version in cluster-file
# * render new kube-vip manifest
# * upload
# * restart kubelet
# * wait for healthy node
# * repeat with next node

import sys
import os
from packaging.version import Version
from lib.kfsTools import read_yaml_file, get_init_master, upload_file, load_template, render_file
from lib.systemctlCommands import systemctl_restart_kubelet
from lib.checkCommands import check_node_health, check_cluster_file_syntax
from time import sleep


def upgrade_kube_vip(cluster_file, unattended_flag=False):
    """
    Reads given cluster-file and upgrades/reinstalls kube-vip with set version

    :param cluster_file: file
    :param unattended_flag: bool
    """

    cluster_files_dir = os.path.dirname(os.path.realpath(__file__))

    # read cluster-file
    cluster_values = read_yaml_file(cluster_file)
    check_cluster_file_syntax(cluster_values)
    
    # needed for initial init-master node setup
    print("Getting data about init-master...")
    node = get_init_master(cluster_values)
    init_master_ip = node["nodeIp"]
    init_master_user = node["sshUser"]
    init_master_key = node["sshKey"]
    
    # we need the version tag from the image-path
    kube_vip_version_string = cluster_values["kubeVip"]["image"]["path"].split(":")[1]
    
    for node in cluster_values["nodes"]:
        if "init-master" == node["type"] or "init-master+worker" == node["type"] or "master" == node["type"] or "master+worker" == node["type"]:
            print("######### BEGIN Kube-Vip upgrade/reinstall on " + node["nodeName"] + " #########")
            config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
            check_config_path = os.path.exists(config_path)

            if not check_config_path:
                os.makedirs(config_path)
            # endif

            # we temporarily add the node's interface to the main cluster_values so we can render the static-pod-manifest for kube-vip
            cluster_values["nodeInterface"] = node["nodeInterface"]
            
            # here we need to check which kube-vip template to use - kube-vip version 0.9+ has renamed a needed environment variable
            # load kube-vip template
            if Version(kube_vip_version_string) < Version("v0.9.0"):
                kube_vip_template = load_template("kubernetes/kube-vip/kube-vip.j2")
            else:
                kube_vip_template = load_template("kubernetes/kube-vip/kube-vip_0.9_plus.j2")
            # endif

            # we create the new file
            render_file(config_path + "/kube-vip.yaml", kube_vip_template, cluster_values)
            
            if unattended_flag:
                print("New Kube-Vip manifest prepared and saved to " + config_path + "/kube-vip.yaml. Unattanded flag set, continuing...")
            else:
                user_input = input("New Kube-Vip manifest prepared and saved to " + config_path + "/kube-vip.yaml" + ". Do you want to continue? (yes/no): ")
                if user_input.lower() in ["yes", "y"]:
                    print("Continuing...")
                else:
                    print("Aborting...")
                    sys.exit(2)
                # endif
            # endif
            
            # we upload the new file
            upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kube-vip.yaml", "/etc/kubernetes/manifests/kube-vip.yaml")

            # and finally we can restart kubelet
            systemctl_restart_kubelet(node["nodeIp"], node["sshUser"], node["sshKey"])

            # we check again until node gets ready
            while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
                print("Node not ready yet, waiting 5 seconds...")
                sleep(5)
            # endwhile
            
            print("Node " + node["nodeName"] + " back online")
            
            print("########## END Kube-Vip upgrade/reinstall on " + node["nodeName"] + " ##########")
        # endif
    # endfor
# enddef
