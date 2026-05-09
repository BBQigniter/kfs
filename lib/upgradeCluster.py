#!/usr/bin/env python

# Rough workflow for upgrading node(s) of cluster
#
# * user updates version in cluster-file
# * scripts checks if configured version is eligible as we know we must not skip minor versions
# * if ok we continue by draining the first node (most probably the init-master)
# * we upgrade containerd if there is an update available and wait until node is ready again
# * we install the selected kubernetes version
# * then we execute "kubeadm upgrade plan" - the output must be checked, if any manual task are needed (mostly not)
# * if ok we execute "kubeadm upgrade apply VERSION"
# * we reload systemd and restat kubelet
# * wait for node to be back online and uncordon node
# * repeat for other nodes - but there we only have to execute "kubeadm upgrade node"


import sys
import os
from packaging.version import Version
from time import sleep
from lib.kubectlCommands import kubectl_get_cluster_version, kubectl_drain_node, kubectl_uncordon_node
from lib.kubeadmCommands import kubeadm_upgrade_plan, kubeadm_upgrade_apply_version, kubeadm_upgrade_node
from lib.systemctlCommands import systemctl_restart_kubelet, systemctl_daemon_reload
from lib.checkCommands import check_node_health, check_and_install_packages, check_is_in_cluster, check_cluster_file_syntax, check_upgrade_scripts
from lib.kfsTools import read_yaml_file, get_minor_version_string, install_packages, get_init_master, load_template, upload_file, render_file


# the upgrade procedure must be in following order
# * upgrade kubeadm on first control-plane node and execute needed commands
# * upgrade kubeadm on the other control-plane nodes and execute needed commands
# * THEN upgrade kubelet and other packages on control-plane nodes
# * then upgrade worker nodes
def upgrade_cluster(cluster_file, unattended_flag=False):
    """
    Reads given cluster-file and runs upgrade workflow if applicable

    :param cluster_file: file
    :param unattended_flag: bool
    """
    # initial vars
    cluster_files_dir = os.path.dirname(os.path.realpath(__file__))
    is_upgrade = True

    # read cluster-file
    cluster_values = read_yaml_file(cluster_file)
    check_cluster_file_syntax(cluster_values)
    
    # needed for initial init-master node setup
    print("Getting data about init-master...")
    node = get_init_master(cluster_values)
    init_master_ip = node["nodeIp"]
    init_master_user = node["sshUser"]
    init_master_key = node["sshKey"]

    print("######### BEGIN upgrading cluster " + cluster_values["cluster"]["name"] + " #########")
    
    minor_version_string = get_minor_version_string(cluster_values["kube"]["version"])
    cluster_values["kube"]["minorVersion"] = minor_version_string

    # load dnf-repo template
    # dnf_repo = load_template("dnf-repo/kubernetes.j2")
    
    config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
    check_config_path = os.path.exists(config_path)

    # load extra kubeletconfiguration templates
    kubeletconfiguration_gc_patch = load_template("kubernetes/kubelet/kubeletconfiguration-garbage-collection.j2")

    if not check_config_path:
        os.makedirs(config_path)
    # endif
    
    ############ BEGIN KUBEADM UPGRADE ON ALL CONTROL-PLANE NODES ############ 
    # We first start to upgrade the init-master
    if "init-master" == node["type"] or "init-master+worker" == node["type"]:
        print("######### BEGIN checking node " + node["nodeName"] + " if already in cluster " + cluster_values["cluster"]["name"] + " #########")
        current_kubernetes_version_string = kubectl_get_cluster_version(node["nodeIp"], node["sshUser"], node["sshKey"])
        # In case something went wrong with the upgrade you can manually set the server version here - but use this only in worst case scenarios!
        # current_kubernetes_version_string = "1.30.9"
        current_kubernete_minor_version_string = get_minor_version_string(current_kubernetes_version_string)
        # before we drain, we check if the node is really part of this cluster
        check_is_in_cluster(init_master_ip, init_master_user, init_master_key, node, is_upgrade=True, unattended=unattended_flag, cluster_name_info=cluster_values["cluster"]["name"])

        # TODO: we must find a way to continue an upgrade in case there went something wrong inbetween
        # If same version - exit
        if Version(current_kubernetes_version_string) == Version(cluster_values["kube"]["version"]):
            print("No upgrade possible because the selected version " + current_kubernetes_version_string + " is currently running! Aborting...")
            sys.exit(1)
        # If it is a downgrade - exit
        elif Version(cluster_values["kube"]["version"]) < Version(current_kubernetes_version_string):
            print("Downgrade to version " + cluster_values["kube"]["version"] + " NOT allowed! Aborting...")
            sys.exit(1)
        # If the upgrade is not skipping a minor version, it's ok
        elif Version(minor_version_string).minor - Version(current_kubernete_minor_version_string).minor == 0 or Version(minor_version_string).minor - Version(
                current_kubernete_minor_version_string).minor == 1:
            print("Upgrade from " + current_kubernetes_version_string + " to " + cluster_values["kube"]["version"] + " allowed!")
            is_upgrade = True
        # anything else is not allowed
        else:
            print("Cluster not eligible for an upgrade from " + current_kubernetes_version_string + " to " + cluster_values["kube"]["version"] + " NOT allowed! Aborting...")
            sys.exit(1)
        # endif

        print("######### END checking node " + node["nodeName"])
        print("    ######### BEGIN upgrading kubeadm only on node " + node["nodeName"] + " #########")
        
        # we do not need to drain the node while upgrading kubeadm (the control-plane components)
        print("Trying to install selected kubeadm-version...")
        install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], ["kubeadm-" + cluster_values["kube"]["version"]], unattended=unattended_flag, kubernetes_version=cluster_values["kube"]["version"])
        
        has_patch = False
        # we check if extra kubelet config-parameters should be set for this node
        if "kubelet" in node:
            if "garbageCollection" in node["kubelet"]:
                # we generate the needed file with a filename like described at https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/control-plane-flags/#patches
                render_file(config_path + "/kubeletconfiguration0.yaml", kubeletconfiguration_gc_patch, node)
                # and we immediately upload it
                upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kubeletconfiguration0.yaml", "/etc/kubernetes/patches/kubeletconfiguration0.yaml")
                has_patch = True
            # endif
        # endif
        
        # next we have to execute kubeadm upgrade plan
        kubeadm_upgrade_plan(node["nodeIp"], node["sshUser"], node["sshKey"], unattended=unattended_flag)

        # if the output is OK we finally upgrade the first node
        print("Applying upgrade now. Do not panic, this can take a while...")
        kubeadm_upgrade_apply_version(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], has_patch=has_patch)
        
        # occasionally the node-health check is too fast - so we have to defer it a little bit
        print("Checking node health (graceperiod ~15s)...")
        sleep(15)
        
        # we check again until node gets ready
        while not check_node_health(node["nodeIp"], node["sshUser"], node["sshKey"], node["nodeName"], is_upgrade=is_upgrade):
            print("Node not ready yet, waiting 5 seconds...")
            sleep(5)
        # endwhile
        
        print("    ######### END upgrading kubeadm only on node " + node["nodeName"] + " ##########")
    # endif
    
    print("Starting to upgrade kubeadm only on other master-nodes...")
    
    # we continue to upgrade kubeadm on the other master nodes
    for node in cluster_values["nodes"]:
        if "init-master" == node["type"] or "init-master+worker" == node["type"]:
            print("Skipping init-master " + node["nodeName"] + " because it was already upgraded. Continuing with next node...")
            continue
        else:
            if "worker" == node["type"]:
                print("Skipping worker-node " + node["nodeName"] + " we have to first upgrade master-nodes. Continuing with next node...")
                continue
            else:
                config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
                check_config_path = os.path.exists(config_path)
    
                if not check_config_path:
                    os.makedirs(config_path)
                # endif
    
                print("    ######### BEGIN upgrading kubeadm only on node " + node["nodeName"] + " #########")
            # endif
        # endif

        # Now the magic begins
        if "master" == node["type"] or "master+worker" == node["type"]:
            # we check if the node is really part of this cluster
            check_is_in_cluster(init_master_ip, init_master_user, init_master_key, node, is_upgrade=True, unattended=unattended_flag, cluster_name_info=cluster_values["cluster"]["name"])

            print("Trying to install selected kubeadm-version...")
            install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], ["kubeadm-" + cluster_values["kube"]["version"]], unattended=unattended_flag, kubernetes_version=cluster_values["kube"]["version"])

            has_patch = False
            # we check if extra kubelet config-parameters should be set for this node
            if "kubelet" in node:
                if "garbageCollection" in node["kubelet"]:
                    # we generate the needed file with a filename like described at https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/control-plane-flags/#patches
                    render_file(config_path + "/kubeletconfiguration0.yaml", kubeletconfiguration_gc_patch, node)
                    # and we immediately upload it
                    upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kubeletconfiguration0.yaml", "/etc/kubernetes/patches/kubeletconfiguration0.yaml")
                    has_patch = True
                # endif
            # endif

            # next we have to execute kubectl upgrade
            print("Applying upgrade now. Do not panic, this can take a while...")
            kubeadm_upgrade_node(node["nodeIp"], node["sshUser"], node["sshKey"], has_patch=has_patch)

            # occasionally the node-health check is too fast - so we have to defer it a little bit
            print("Checking node health (graceperiod ~15s)...")
            sleep(15)

            # we check again until node gets ready
            while not check_node_health(node["nodeIp"], node["sshUser"], node["sshKey"], node["nodeName"], is_upgrade=is_upgrade):
                print("Node not ready yet, waiting 5 seconds...")
                sleep(5)
            # endwhile
        # endif
                
        print("    ######### END upgrading kubeadm only on node " + node["nodeName"] + " ##########")
    # endfor
    ############ END KUBEADM UPGRADE ON ALL CONTROL-PLANE NODES ############
    
    print("Starting to upgrade rest of Kubernetes packages on all master nodes...")
    
    ############ BEGIN UPGRADING REST OF KUBERNETES-PACKAGES ON ALL CONTROL-PLANE NODES ############
    for node in cluster_values["nodes"]:
        if "master" in node["type"]:
            config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
            check_config_path = os.path.exists(config_path)

            if not check_config_path:
                os.makedirs(config_path)
            # endif

            print("    ######### BEGIN upgrading rest of kubernetes related packages on node " + node["nodeName"] + " #########")
        else:
            print("Skipping worker-node " + node["nodeName"] + " because we have to upgrade the rest of the Kubernetes packages on the master nodes first. Continuing with next node...")
            continue
        # endif
        
        # Now the magic begins - before we drain the node, we will check if some upgrade check-script must be executed
        check_upgrade_scripts(node, cluster_files_dir)

        # we have to skip draining if it's only a one-node-cluster - else we have troubles especially with special single pod-manifests
        if len(cluster_values["nodes"]) > 1:
            # First we drain the node this might take a while
            print("Draining node " + node["nodeName"] + " this might take a while depending on the amount of workloads...")
            if kubectl_drain_node(node["nodeIp"], node["sshUser"], node["sshKey"], node["nodeName"]):
                print("Successfully drained the node " + node["nodeName"] + ". Continuing...")
            # endif
        else:
            print("Draining node " + node["nodeName"] + " skipped because of one-node-cluster setup!")
            print("You have to check manually if everything all none-system workloads get back online after the upgrade is finished!")
            print("Continuing...")
        # endif

        # we now try to upgrade containerd
        print("Trying to install latest containerd.io package if there is one available...")
        install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], ["containerd.io"], unattended=unattended_flag, kubernetes_version=cluster_values["kube"]["version"])

        # occasionally this check is too fast - so we have to defer it a little bit
        print("Checking node health (graceperiod ~30s)...")
        sleep(30)

        # we check again until node gets ready
        while not check_node_health(node["nodeIp"], node["sshUser"], node["sshKey"], node["nodeName"], is_upgrade=is_upgrade):
            print("Node not ready yet, waiting 15 seconds...")
            sleep(15)
        # endwhile

        # we now install the other packages needed for a full upgrade
        check_and_install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], unattended=unattended_flag, is_upgrade=is_upgrade)

        # we have to reload systemd now
        systemctl_daemon_reload(node["nodeIp"], node["sshUser"], node["sshKey"])

        # and finally we can restart kubelet
        systemctl_restart_kubelet(node["nodeIp"], node["sshUser"], node["sshKey"])

        # occasionally the node-health check is too fast - so we have to defer it a little bit
        print("Checking node health (graceperiod ~30s)...")
        sleep(30)

        # we check again until node gets ready
        while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"], is_upgrade=is_upgrade):
            print("Node not ready yet, waiting 15 seconds...")
            sleep(15)
        # endwhile

        kubectl_uncordon_node(init_master_ip, init_master_user, init_master_key, node["nodeName"])

        # occasionally the node-health check is too fast - so we have to defer it a little bit
        print("Checking node health...")
        sleep(5)

        # TODO: We probably also should check for certain needed pods/workloads that they are running before continuing
        # to make sure that everything is alright
        while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
            print("Node not ready yet, waiting 5 seconds...")
            sleep(5)
        # endwhile

        print("    ######### END upgrading upgrading rest of kubernetes related packages on node " + node["nodeName"] + " ##########")
    # endfor
    ############ END UPGRADING REST OF KUBERNETES-PACKAGES ON ALL CONTROL-PLANE NODES ############
    
    print("Starting to upgrade other dedicated worker-nodes, if there are any...")
    
    # finally we only upgrade worker nodes
    for node in cluster_values["nodes"]:
        if "master" in node["type"]:
            print("Skipping master-node " + node["nodeName"] + " because it was already upgraded. Continuing with next node...")
            continue
        else:
            config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
            check_config_path = os.path.exists(config_path)

            if not check_config_path:
                os.makedirs(config_path)
            # endif

            print("    ######### BEGIN upgrading worker node " + node["nodeName"] + " #########")
        # endif
        
        if "worker" == node["type"]:
            # before we drain, we check if the node is really part of this cluster
            check_is_in_cluster(init_master_ip, init_master_user, init_master_key, node, is_upgrade=True, unattended=unattended_flag, cluster_name_info=cluster_values["cluster"]["name"])

            # Now the magic begins - before we drain the node, we will check if some upgrade check-script must be executed
            check_upgrade_scripts(node, cluster_files_dir)
            
            # First we drain the node this might take a while
            print("Draining node " + node["nodeName"] + " this might take a while depending on the amount of workloads...")
            if kubectl_drain_node(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
                print("Successfully drained the node " + node["nodeName"] + ". Continuing...")
            # endif

            # we now try to upgrade containerd
            install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], ["containerd.io"], unattended=unattended_flag, kubernetes_version=cluster_values["kube"]["version"])

            # occasionally the node-health check is too fast - so we have to defer it a little bit
            print("Checking node health...")
            sleep(5)
            
            # we check again until node gets ready
            while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"], is_upgrade=is_upgrade):
                print("Node not ready yet, waiting 15 seconds...")
                sleep(15)
            # endwhile
            
            print("Trying to install selected kubeadm-version...")
            install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], ["kubeadm-" + cluster_values["kube"]["version"]], unattended=unattended_flag, kubernetes_version=cluster_values["kube"]["version"])
            
            has_patch = False
            # we check if extra kubelet config-parameters should be set for this node
            if "kubelet" in node:
                if "garbageCollection" in node["kubelet"]:
                    # we generate the needed file with a filename like described at https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/control-plane-flags/#patches
                    render_file(config_path + "/kubeletconfiguration0.yaml", kubeletconfiguration_gc_patch, node)
                    # and we immediately upload it
                    upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kubeletconfiguration0.yaml", "/etc/kubernetes/patches/kubeletconfiguration0.yaml")
                    has_patch = True
                # endif
            # endif

            # next we have to execute kubectl upgrade plan
            print("Applying upgrade now. Do not panic, this can take a while...")
            kubeadm_upgrade_node(node["nodeIp"], node["sshUser"], node["sshKey"], has_patch=has_patch)

            # we now install the other packages needed for a full upgrade
            check_and_install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], unattended=unattended_flag, is_upgrade=is_upgrade)
            
            # we have to reload systemd now
            systemctl_daemon_reload(node["nodeIp"], node["sshUser"], node["sshKey"])

            # and finally we can restart kubelet
            systemctl_restart_kubelet(node["nodeIp"], node["sshUser"], node["sshKey"])

            # occasionally the node-health check is too fast - so we have to defer it a little bit
            print("Checking node health (~15s graceperiod)...")
            sleep(15)
            
            # we check again until node gets ready
            while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"], is_upgrade=is_upgrade):
                print("Node not ready yet, waiting 5 seconds...")
                sleep(5)
            # endwhile

            kubectl_uncordon_node(init_master_ip, init_master_user, init_master_key, node["nodeName"])

            # occasionally the node-health check is too fast - so we have to defer it a little bit
            print("Checking node health...")
            sleep(5)
            
            # TODO: We probably also should check for certain needed pods/workloads that they are running before continuing
            # to make sure that everything is alright
            while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
                print("Node not ready yet, waiting 5 seconds...")
                sleep(5)
            # endwhile
        # endif
        
        print("    ######### END upgrading worker node " + node["nodeName"] + " ##########")
    # endfor

    print("########## END upgrading cluster " + cluster_values["cluster"]["name"] + " ##########")
# enddef
