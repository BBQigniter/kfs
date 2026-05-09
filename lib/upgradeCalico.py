#!/usr/bin/env python

# rough workflow
#
# * change version in cluster-file
# * download new tigera-operator.yaml
# * change registry in file
# * upload and apply
# * wait for healthy nodes

import sys
import os
import subprocess
from packaging.version import Version
from lib.kfsTools import read_yaml_file, upload_file, inplace_file_change, get_init_master
from lib.kubectlCommands import kubectl_install_manifest
from lib.checkCommands import check_cluster_file_syntax, check_workload
from time import sleep


def upgrade_calico(cluster_file, unattended_flag=False):
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
    
    config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
    check_config_path = os.path.exists(config_path)

    if not check_config_path:
        os.makedirs(config_path)
    # endif

    # TODO:
    #  * add check if upgrade applicable - no use if the same version is getting installed
    #  * add check that there is no downgrade executed accidentally!

    calico_version_string = cluster_values["calico"]["tigera"]["version"]

    if unattended_flag:
        print("This will try to upgrade the Tigera-Operator to " + calico_version_string + ". Unattended flag set, continuing...")
    else:
        user_input = input("This will try to upgrade the Tigera-Operator to " + calico_version_string + ". Continue? (yes/no): ")
        if user_input.lower() in ["yes", "y"]:
            print("Proceeding with upgrading Tigera-Operator...")
        else:
            print("Aborting...")
            sys.exit(2)
        # endif
    # endif
    
    # we will keep to the upgrade procedure explained here - https://docs.tigera.io/calico/latest/operations/upgrading/kubernetes-upgrade#upgrading-an-installation-that-uses-the-operator
    # in case of 3.30+ we need to download 2 files (operator-crds.yaml and tigera-operator.yaml)
    if Version(calico_version_string) >= Version("v3.30.0"):
        # wget_return_code = subprocess.call("wget -q -P " + config_path + " https://raw.githubusercontent.com/projectcalico/calico/" + calico_version_string + "/manifests/operator-crds.yaml -O operator-crds.yaml", shell=True)
        # curl -L -o ./clusters/test-cluster/node-1.home.arpa/operator-crds.yaml https://raw.githubusercontent.com/projectcalico/calico/v3.30.0/manifests/operator-crds.yaml
        curl_return_code = subprocess.call("curl -s -L -o " + config_path + "/operator-crds.yaml https://raw.githubusercontent.com/projectcalico/calico/" + calico_version_string + "/manifests/operator-crds.yaml", shell=True)
        if curl_return_code == 0:
            print("Downloaded operator-crds.yaml successfully")
        else:
            print("Downloaded operator-crds.yaml failed")
            sys.exit(1)
        # endif
        upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/operator-crds.yaml", "/root/operator-crds.yaml")
        # we apply/create the downloaded operator-crds
        kubectl_install_manifest(node["nodeIp"], node["sshUser"], node["sshKey"], "/root/operator-crds.yaml", server_side=True, force_conflicts=True)
    # endif

    # we have to download tigera-operator.yaml locally and edit a line
    print("Downloading tigera-operator.yaml...")

    # wget_return_code = subprocess.call("wget -q -P " + config_path + " https://raw.githubusercontent.com/projectcalico/calico/" + calico_version_string + "/manifests/tigera-operator.yaml -O tigera-operator.yaml", shell=True)
    # curl -L -o ./clusters/test-cluster/node-1.home.arpa/tigera-operator.yaml https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/tigera-operator.yaml
    curl_return_code = subprocess.call("curl -s -L -o " + config_path + "/tigera-operator.yaml https://raw.githubusercontent.com/projectcalico/calico/" + calico_version_string + "/manifests/tigera-operator.yaml", shell=True)
    if curl_return_code == 0:
        print("Downloaded tigera-operator.yaml successfully")
    else:
        print("Downloaded tigera-operator.yaml failed")
        sys.exit(1)
    # endif
    inplace_file_change(config_path + "/tigera-operator.yaml", "image: quay.io/", "image: " + cluster_values["calico"]["tigera"]["registry"])
    upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/tigera-operator.yaml", "/root/tigera-operator.yaml")
    # we apply/create the downloaded tigera-operator
    kubectl_install_manifest(node["nodeIp"], node["sshUser"], node["sshKey"], "/root/tigera-operator.yaml", server_side=True, force_conflicts=True)
    
    # we check that the tigera-operator and all other needed workloads get healthy with:
    # check_workload(init_master_ip, user, key, workload_type, workload_namespace, workload_name=None, image_version_tag=None)
    # we want to prevent short-circuiting, so we use the all-method
    #
    #     # the extra calico-apiserver was added with version 3.20 - see https://docs.tigera.io/calico/latest/operations/install-apiserver  ---- some clusters might not have this enabled from start on and so might not have this workload at all
    #              check_workload(init_master_ip, init_master_user, init_master_key, workload_type="deployment", workload_namespace="calico-apiserver", workload_name="calico-apiserver", image_version_tag=cluster_values["calico"]["tigera"]["version"]),
    while not all([check_workload(init_master_ip, init_master_user, init_master_key, workload_type="deployment", workload_namespace="tigera-operator", workload_name="tigera-operator"),
                   check_workload(init_master_ip, init_master_user, init_master_key, workload_type="deployment,daemonset", workload_namespace="calico-system", image_version_tag=cluster_values["calico"]["tigera"]["version"])]):
        print("Workloads not ready yet, waiting 5 seconds...")
        sleep(5)
    # endwhile
    
    print("All workloads seem to be healthy - Calico Upgrade finished")
# enddef

