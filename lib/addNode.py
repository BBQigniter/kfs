#!/usr/bin/env python

# Rough workflow for adding node(s) to cluster
# * user updates cluster-file
# * looping through nodes and getting details from init-master
# * if node found that is not part of cluster create needed token, cert-hash and if needed a cert-key (in case of an additional master-node)
# * also check if prerequisites are available (correct package-versions installed, etc.)
# * TODO: check that --initial-cluster parameter is on all master-nodes correctly configured. newly added master nodes, have mostly a correctly configured paramater
#     but previously added nodes, might miss the newer ones.

import sys
import os
from time import sleep
from packaging.version import Version
from lib.kubectlCommands import kubectl_untaint_node, kubectl_label_node, enable_kubectl
from lib.kubeadmCommands import kubeadm_get_kube_cert_key, kubeadm_get_kube_token_and_hash, kubadm_join_cluster, kubeadm_get_images_list
from lib.checkCommands import check_node_health, check_and_install_packages, check_is_in_cluster, check_cluster_file_syntax, check_swap, check_modprobe, check_sysctl, check_containerd
from lib.kfsTools import read_yaml_file, load_template, upload_file, render_file, get_minor_version_string, get_init_master, fix_containerd_config, create_kubernetes_patches_folder


def add_nodes(cluster_file, unattended_flag=False):
    """
    Add node to existing cluster
    
    :param cluster_file: file
    :param unattended_flag: bool
    :return: bool
    """
    
    cluster_files_dir = os.path.dirname(os.path.realpath(__file__))
    
    nodes_add_success = False
    
    # read cluster-file
    cluster_values = read_yaml_file(cluster_file)
    check_cluster_file_syntax(cluster_values)
    
    print("######### BEGIN adding node(s) to cluster " + cluster_values["cluster"]["name"] + " #########")
    
    # add some default values needed later
    minor_version_string = get_minor_version_string(cluster_values["kube"]["version"])
    cluster_values["kube"]["minorVersion"] = minor_version_string
    
    # needed for initial init-master node setup
    print("Getting data about init-master...")
    node = get_init_master(cluster_values)
    init_master_ip = node["nodeIp"]
    init_master_user = node["sshUser"]
    init_master_key = node["sshKey"]
    
    # version check to choose correct kube-vip template
    # we need the version tag from the image-path
    kube_vip_version_string = cluster_values["kubeVip"]["image"]["path"].split(":")[1]
    
    # load kube-vip template
    if Version(kube_vip_version_string) < Version("0.9.0"):
        kube_vip_template = load_template("kubernetes/kube-vip/kube-vip.j2")
    else:
        kube_vip_template = load_template("kubernetes/kube-vip/kube-vip_0.9_plus.j2")
    # endif
    
    # version check to choose correct kubernetes templates
    if Version(cluster_values["kube"]["version"]) < Version("1.31.0"):
        # load join-master-config template
        join_master_config = load_template("kubernetes/kubeadm-configs/join-master-config.j2")
        
        # load join-worker-config template
        join_worker_config = load_template("kubernetes/kubeadm-configs/join-worker-config.j2")
    else:
        # load join-master-config template
        join_master_config = load_template("kubernetes/kubeadm-configs/join-master-config_1.31_plus.j2")

        # load join-worker-config template
        join_worker_config = load_template("kubernetes/kubeadm-configs/join-worker-config_1.31_plus.j2")
    # endif
    
    # load audit-policy template used by apiserver
    audit_policy_template = load_template("kubernetes/apiserver/audit-policy.j2")

    # load extra kubeletconfiguration templates
    kubeletconfiguration_gc_patch = load_template("kubernetes/kubelet/kubeletconfiguration-garbage-collection.j2")
    
    # load dnf-repo template
    # dnf_repo = load_template("dnf-repo/kubernetes.j2")
    
    # check if kubeadm kubelet kubectl kubernetes-cni containerd.io is installed correctly
    check_and_install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], unattended=unattended_flag)
    
    if check_node_health(node["nodeIp"], node["sshUser"], node["sshKey"], node["nodeName"]):
        print("Init-Master OK, continuing...")
    else:
        print("Node not ready yet or not part of this cluster, pleasec check! Exiting...")
        sys.exit(1)
    # endwhile
    
    for node in cluster_values["nodes"]:
        # if master node, check if node is ready and make sure node is ready and part of the selected cluster
        # if all ok continue
        # else add node (create needed files)
        # if worker node, check if node is ready and make sure node is ready and part of the selected cluster
        # if all ok continue
        # else add node
        
        if "init-master" == node["type"] or "init-master+worker" == node["type"]:
            print("Skipping init-master because we cannot add a init-master. Continuing with next node...")
            continue  # go to next node in list
        else:
            print("######### BEGIN checking node " + node["nodeName"] + " if already in cluster " + cluster_values["cluster"]["name"] + " #########")
            config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
            check_config_path = os.path.exists(config_path)
            
            if not check_config_path:
                os.makedirs(config_path)
            # endif
        # endif
        
        if init_master_ip is not None:
            if "master" == node["type"] or "master+worker" == node["type"]:
                # check if kubeadm kubelet kubectl kubernetes-cni containerd.io is installed and install them if needed and confirmed
                check_and_install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], unattended=unattended_flag)
                check_swap(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_sysctl(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_modprobe(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_containerd(node["nodeIp"], node["sshUser"], node["sshKey"])
                
                if check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
                    print("FOUND OK master, continuing...")
                    print("######### END checking node " + node["nodeName"] + " if already in cluster " + cluster_values["cluster"]["name"] + " #########")
                    continue
                else:
                    # possibly a new node
                    check_is_in_cluster(init_master_ip, init_master_user, init_master_key, node, unattended=unattended_flag)
                    print("Node might not be part of cluster yet")
                    
                    print("    ######### BEGIN adding node " + node["nodeName"] + " to cluster " + cluster_values["cluster"]["name"] + " #########")
                    
                    # we fix a possible issue with the sandbox_image parameter in the containerd's config.toml
                    kubeadm_images = kubeadm_get_images_list(node["nodeIp"], node["sshUser"], node["sshKey"])
                    print("Trying to fix containerd's config.toml")
                    fix_containerd_config(node["nodeIp"], node["sshUser"], node["sshKey"], kubeadm_images, cluster_values["kube"]["image"]["repository"], config_path)
                    
                    # we temporarily add the node's interface to the main cluster_values so we can render the static-pod-manifest for kube-vip
                    cluster_values["nodeInterface"] = node["nodeInterface"]
                    
                    render_file(config_path + "/kube-vip.yaml", kube_vip_template, cluster_values)
                    
                    # render the audit-policy manifest - currently no change of paths or so implemented
                    render_file(config_path + "/audit-policy.yaml", audit_policy_template, cluster_values)
                    
                    # we need: kube.token, kube.cert.hash, kube.cert.key, nodeName, nodeIp
                    # we temporarily add those values to the main cluster_values so we can render the static-pod-manifest for kube-vip
                    cluster_values["nodeName"] = node["nodeName"]
                    cluster_values["nodeIp"] = node["nodeIp"]
                    (kube_token, kube_cert_hash) = kubeadm_get_kube_token_and_hash(init_master_ip, init_master_user, init_master_key)
                    cluster_values["kube"]["token"] = kube_token
                    # we have to init kube.cert because it doesn't exist yet
                    cluster_values["kube"]["cert"] = {}
                    cluster_values["kube"]["cert"]["hash"] = kube_cert_hash
                    cluster_values["kube"]["cert"]["key"] = kubeadm_get_kube_cert_key(init_master_ip, init_master_user, init_master_key)
                    
                    render_file(config_path + "/join-master-config.yaml", join_master_config, cluster_values)
                    
                    # we check if extra kubelet config-parameters should be set for this node
                    if "kubelet" in node:
                        if "garbageCollection" in node["kubelet"]:
                            # we generate the needed file with a filename like described at https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/control-plane-flags/#patches
                            render_file(config_path + "/kubeletconfiguration0.yaml", kubeletconfiguration_gc_patch, node)
                            # and we immediately upload it
                            upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kubeletconfiguration0.yaml", "/etc/kubernetes/patches/kubeletconfiguration0.yaml")
                        # endif
                    else:
                        # we have to create the folder anyway even if there are no patches uploaded since we added the config-parameter to the join/init-files
                        create_kubernetes_patches_folder(node["nodeIp"], node["sshUser"], node["sshKey"])
                    # endif
                    
                    # upload files to corresponding folders
                    # kube-vip.yaml -> /etc/kubernetes/manifests
                    # join-master-config.yaml -> /root
                    upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kube-vip.yaml", "/etc/kubernetes/manifests/kube-vip.yaml")
                    upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/join-master-config.yaml", "/root/join-master-config.yaml")
                    upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/audit-policy.yaml", "/etc/kubernetes/audit-policy.yaml")
                    
                    # execute kubeadm-command for joining a master node
                    kubadm_join_cluster(node["nodeIp"], node["sshUser"], node["sshKey"], "/root/join-master-config.yaml")
                    
                    # check if all ok
                    while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
                        print("Node not ready yet, waiting 5 seconds...")
                        sleep(5)
                    # endwhile
                    
                    # create .kube-folder in /root and link .kube/config to /etc/kubernetes/admin.conf and enable bash-completion
                    enable_kubectl(node["nodeIp"], node["sshUser"], node["sshKey"])
                    
                    if node["type"] == "master+worker":
                        print("Untainting node...")
                        kubectl_untaint_node(init_master_ip, init_master_user, init_master_key, node["nodeName"])
                        print("Setting worker node role label...")
                        kubectl_label_node(init_master_ip, init_master_user, init_master_key, node["nodeName"], node_labels=["node-role.kubernetes.io/worker="])
                    # endif
                    
                    if "nodeLabels" in node:
                        print("Setting additional node-labels...")
                        kubectl_label_node(init_master_ip, init_master_user, init_master_key, node["nodeName"], node_labels=node["nodeLabels"])
                    # endif
                    
                    nodes_add_success = True
                    print("    ######### END adding node " + node["nodeName"] + " to cluster " + cluster_values["cluster"]["name"] + " #########")
                # endif
            # endif
            
            if "worker" == node["type"]:
                # check if kubeadm kubelet kubectl kubernetes-cni containerd.io is installed and install them if needed and confirmed
                check_and_install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], unattended=unattended_flag)
                check_swap(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_sysctl(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_modprobe(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_containerd(node["nodeIp"], node["sshUser"], node["sshKey"])
                
                if check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
                    print("FOUND OK worker, continuing...")
                    print("######### END checking node if already in cluster " + cluster_values["cluster"]["name"] + " #########")
                    continue
                else:
                    # possibly a new node
                    check_is_in_cluster(init_master_ip, init_master_user, init_master_key, node, unattended=unattended_flag)
                    print("Node might not be part of cluster yet")
                    
                    print("    ######### BEGIN adding node " + node["nodeName"] + " to cluster " + cluster_values["cluster"]["name"] + " #########")
                    
                    # we fix a possible issue with the sandbox_image parameter in the containerd's config.toml
                    kubeadm_images = kubeadm_get_images_list(node["nodeIp"], node["sshUser"], node["sshKey"])
                    print("Trying to fix containerd's config.toml")
                    fix_containerd_config(node["nodeIp"], node["sshUser"], node["sshKey"], kubeadm_images, cluster_values["kube"]["image"]["repository"], config_path)
                    
                    # we need: kube.token, kube.cert.hash, nodeName, nodeIp
                    # we temporarily add those values to the main cluster_values so we can render the static-pod-manifest for kube-vip
                    cluster_values["nodeName"] = node["nodeName"]
                    cluster_values["nodeIp"] = node["nodeIp"]
                    (kube_token, kube_cert_hash) = kubeadm_get_kube_token_and_hash(init_master_ip, init_master_user, init_master_key)
                    cluster_values["kube"]["token"] = kube_token
                    cluster_values["kube"]["cert"] = {}
                    cluster_values["kube"]["cert"]["hash"] = kube_cert_hash
                    
                    render_file(config_path + "/join-worker-config.yaml", join_worker_config, cluster_values)
                    
                    # we check if extra kubelet config-parameters should be set for this node
                    if "kubelet" in node:
                        if "garbageCollection" in node["kubelet"]:
                            # we generate the needed file with a filename like described at https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/control-plane-flags/#patches
                            render_file(config_path + "/kubeletconfiguration0.yaml", kubeletconfiguration_gc_patch, node)
                            # and we immediately upload it
                            upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kubeletconfiguration0.yaml", "/etc/kubernetes/patches/kubeletconfiguration0.yaml")
                        # endif
                    else:
                        # we have to create the folder anyway even if there are no patches uploaded since we added the config-parameter to the join/init-files
                        create_kubernetes_patches_folder(node["nodeIp"], node["sshUser"], node["sshKey"])
                    # endif
                    
                    # upload files to corresponding folders
                    # join-worker-config.yaml -> /root
                    upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/join-worker-config.yaml", "/root/join-worker-config.yaml")
                    
                    # execute kubeadm-command for joining a worker node
                    kubadm_join_cluster(node["nodeIp"], node["sshUser"], node["sshKey"], "/root/join-worker-config.yaml")
                    
                    # check if all ok on init-master and continue
                    while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
                        print("Node not ready yet, waiting 5 seconds...")
                        sleep(5)
                    # endwhile
                    
                    print("Setting worker node role label...")
                    kubectl_label_node(init_master_ip, init_master_user, init_master_key, node["nodeName"], node_labels=["node-role.kubernetes.io/worker="])
                    
                    if "nodeLabels" in node:
                        print("Setting additional node-labels...")
                        kubectl_label_node(init_master_ip, init_master_user, init_master_key, node["nodeName"], node_labels=node["nodeLabels"])
                    # endif
                    
                    nodes_add_success = True
                    print("    ######### END adding node " + node["nodeName"] + " to cluster " + cluster_values["cluster"]["name"] + " #########")
                # endif
            # endif
        # endif
    # endfor
    
    return nodes_add_success
# enddef

