#!/usr/bin/env python

# Rough workflow for creating cluster
# * reads cluster-file
# * tries to check several things like, if node is part of a cluster or has already kubernetes parts running (TODO)
# * generates token for init-master
# * creates needed files and uploads to node
# * initializes first node and waits for it to be ready
# * looping through nodes and adding them to the cluster according to their set type

import paramiko
import sys
import os
import subprocess
from packaging.version import Version
from time import sleep
from lib.kubectlCommands import kubectl_untaint_node, kubectl_label_node, kubectl_install_manifest, enable_kubectl
from lib.kubeadmCommands import kubeadm_get_kube_cert_key, kubeadm_get_kube_token_and_hash, kubadm_join_cluster, kubeadm_get_images_list
from lib.systemctlCommands import systemctl_restart_kubelet, systemctl_autostart_containerd_kubelet
from lib.checkCommands import check_node_health, check_and_install_packages, check_is_in_cluster, check_cluster_file_syntax, check_swap, check_modprobe, check_sysctl, check_containerd
from lib.kfsTools import read_yaml_file, write_yaml_file, load_template, upload_file, render_file, get_minor_version_string, inplace_file_change, get_init_master, fix_containerd_config, create_kubernetes_patches_folder, stream_output


def create_cluster(cluster_file, unattended_flag=False):
    """
    Start creating a Kubernetes cluster.

    :param cluster_file: file
    :param unattended_flag: boolean
    :return: bool
    """
    
    cluster_create_success = False
    cluster_files_dir = os.path.dirname(os.path.realpath(__file__))
    
    def create_kube_token(init_master_ip, user, key):
        # connect to init-master and execute `kubeadm token generate`
        # extract needed token from output - outputs a single line thankfully
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(init_master_ip, username=user, key_filename=key)

        print("Generating first token for init-master-config!")
        stdin, stdout, stderr = client.exec_command('kubeadm token generate')
        cmd_output, cmd_errors = stream_output(stdout)
        # cmd_output, cmd_errors = output_to_list(stdout, stderr)

        if stdout.channel.recv_exit_status() == 0:
            # still we output for warnings
            if len(cmd_errors) > 0:
                print("There seem to be some warnings, please check!")
                print("\n".join(cmd_errors))
            # endif
            
            init_token = "".join(cmd_output).strip()
            print("Token generated: " + init_token)
            
            client.close()
            return init_token
        else:
            print("Something went wrong, please check!")
            print("\n".join(cmd_output))
            print("\n".join(cmd_errors))
            client.close()
            sys.exit(2)
        # endif
    # enddef

    def kubadm_init_cluster(init_master_ip, user, key):
        cluster_init_success = False

        # will execute following command on main-node
        # kubeadm init --upload-certs --config="/root/init-master-config.yaml"
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(init_master_ip, username=user, key_filename=key)

        print("\nInitializing first node - this can take a while...")
        systemctl_autostart_containerd_kubelet(init_master_ip, user, key)

        stdin, stdout, stderr = client.exec_command('kubeadm init --upload-certs --config="/root/init-master-config.yaml"')
        cmd_output, cmd_errors = stream_output(stdout)
        # cmd_output, cmd_errors = output_to_list(stdout, stderr)
        
        # with later kubeadm versions warnings are also posted to stderr - so the better option is to check for the exit-code
        #if len(cmd_output) > 0 and len(cmd_errors) == 0:
        if stdout.channel.recv_exit_status() == 0:
            print("\n".join(cmd_output))
            cluster_init_success = True

            # still we output for warnings
            if len(cmd_errors) > 0:
                print("There seem to be some warnings, please check!")
                print("\n".join(cmd_errors))
            # endif

            client.close()

            return cluster_init_success
        else:
            print("Something went wrong, please check!")
            print("\n".join(cmd_output))
            print("Stderr output:\n")
            print("\n".join(cmd_errors))
            
            client.close()
            sys.exit(2)
        # endif
    # enddef

    # read cluster-file
    cluster_values = read_yaml_file(cluster_file)
    check_cluster_file_syntax(cluster_values)

    # needed for initial init-master node setup
    print("Getting data about init-master...")
    node = get_init_master(cluster_values)
    init_master_ip = node["nodeIp"]
    init_master_user = node["sshUser"]
    init_master_key = node["sshKey"]

    print("######### BEGIN Cluster-Setup for " + cluster_values["cluster"]["name"] + " #########")

    # add some default values needed later
    minor_version_string = get_minor_version_string(cluster_values["kube"]["version"])
    cluster_values["kube"]["minorVersion"] = minor_version_string

    # load audit-policy template used by apiserver
    audit_policy_template = load_template("kubernetes/apiserver/audit-policy.j2")

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
        # load init-master-config template
        init_master_config = load_template("kubernetes/kubeadm-configs/init-master-config.j2")
        
        # load join-master-config template
        join_master_config = load_template("kubernetes/kubeadm-configs/join-master-config.j2")
        
        # load join-worker-config template
        join_worker_config = load_template("kubernetes/kubeadm-configs/join-worker-config.j2")
    else:
        # load init-master-config template
        init_master_config = load_template("kubernetes/kubeadm-configs/init-master-config_1.31_plus.j2")

        # load join-master-config template
        join_master_config = load_template("kubernetes/kubeadm-configs/join-master-config_1.31_plus.j2")

        # load join-worker-config template
        join_worker_config = load_template("kubernetes/kubeadm-configs/join-worker-config_1.31_plus.j2")
    # endif

    # load calico custom-resource template
    calico_tigera_custom_resource = load_template("kubernetes/calico/custom-resource.j2")
    
    # load extra kubeletconfiguration templates
    kubeletconfiguration_gc_patch = load_template("kubernetes/kubelet/kubeletconfiguration-garbage-collection.j2")

    # load dnf-repo template
    # dnf_repo = load_template("dnf-repo/kubernetes.j2")
    
    # we first set up the init-master
    print("######### BEGIN Node-Setup for " + node["nodeName"] + " #########")
    print("######### Node will be added as type: " + node["type"])
    config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
    check_config_path = os.path.exists(config_path)

    if not check_config_path:
        os.makedirs(config_path)
    # endif

    # create init-master-config.yaml - also we need a special kube-vip.yaml for the init-master - keyword "super-admin.conf"
    if "init-master" == node["type"] or "init-master+worker" == node["type"]:
        # we temporarily add the node's interface to the main cluster_values so we can render the static-pod-manifest for kube-vip
        cluster_values["nodeInterface"] = node["nodeInterface"]
        
        # render the kube-vip manifest
        render_file(config_path + "/kube-vip.yaml", kube_vip_template, cluster_values)
        
        # render the audit-policy manifest - currently no change of paths or so implemented
        render_file(config_path + "/audit-policy.yaml", audit_policy_template, cluster_values)

        # we now read the generated file and replace one value if the selected version is 1.29 or newer
        if Version(minor_version_string) >= Version("1.29"):
            print("Setting up initial kube-vip static-pod with 'super-admin.conf'...")
            kube_vip_yaml = read_yaml_file(config_path + "/kube-vip.yaml")
            kube_vip_yaml["spec"]["volumes"][0]["hostPath"]["path"] = "/etc/kubernetes/super-admin.conf"
            write_yaml_file(kube_vip_yaml, config_path + "/kube-vip.yaml")

        # check if kubeadm kubelet kubectl kubernetes-cni containerd.io is installed and install them if needed and confirmed
        check_and_install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], unattended=unattended_flag)
        check_swap(node["nodeIp"], node["sshUser"], node["sshKey"])
        check_sysctl(node["nodeIp"], node["sshUser"], node["sshKey"])
        check_modprobe(node["nodeIp"], node["sshUser"], node["sshKey"])
        check_containerd(node["nodeIp"], node["sshUser"], node["sshKey"])

        # check if node is not already part of a cluster
        check_is_in_cluster(init_master_ip, init_master_user, init_master_key, node, unattended=unattended_flag)
        
        # TODO: we have to get the sandbox-image version and configure containerd's config.toml before continuing
        #   creating a default config.toml will set the default image path in the config, like 'sandbox_image = "registry.k8s.io/pause:3.8"'
        #   which will break the setup if this image cannot be downloaded. Unfortunately it looks like this cannot be handled
        #   via the templates
        kubeadm_images = kubeadm_get_images_list(node["nodeIp"], node["sshUser"], node["sshKey"])
        print("Trying to fix containerd's config.toml")
        fix_containerd_config(node["nodeIp"], node["sshUser"], node["sshKey"], kubeadm_images, cluster_values["kube"]["image"]["repository"], config_path)

        # we need: kube.token, nodeName, nodeIp
        # we temporarily add those values to the main cluster_values so we can render the static-pod-manifest for kube-vip
        cluster_values["nodeName"] = node["nodeName"]
        cluster_values["nodeIp"] = node["nodeIp"]
        cluster_values["kube"]["token"] = create_kube_token(node["nodeName"], node["sshUser"], node["sshKey"])

        render_file(config_path + "/init-master-config.yaml", init_master_config, cluster_values)

        # generating custom-resource.yaml - we only need to do this once and only on the init-master
        render_file(config_path + "/calico_tigera_custom_resource.yaml", calico_tigera_custom_resource, cluster_values)
        
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
        # audit-policy -> /etc/kubernetes
        # kube-vip.yaml -> /etc/kubernetes/manifests
        # init-master-config.yaml -> /root
        # calico_tigera_custom_resource.yaml /root
        upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/audit-policy.yaml", "/etc/kubernetes/audit-policy.yaml")
        upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kube-vip.yaml", "/etc/kubernetes/manifests/kube-vip.yaml")
        upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/init-master-config.yaml", "/root/init-master-config.yaml")
        upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/calico_tigera_custom_resource.yaml", "/root/calico_tigera_custom_resource.yaml")

        # execute kubeadm-command for creating first node
        kubadm_init_cluster(node["nodeIp"], node["sshUser"], node["sshKey"])

        # change kube-vip manifest to use normal admin.conf and restart kubelet
        # we now read the generated kube-vip manifest file agin and replace one value back to default if the selected version is 1.29 or newer
        if Version(minor_version_string) >= Version("1.29"):
            print("Node is initialized, we can change the kube-vip static-pod back to use the normal admin.conf...")
            kube_vip_yaml = read_yaml_file(config_path + "/kube-vip.yaml")
            kube_vip_yaml["spec"]["volumes"][0]["hostPath"]["path"] = "/etc/kubernetes/admin.conf"
            write_yaml_file(kube_vip_yaml, config_path + "/kube-vip.yaml")
            # we upload the file again
            upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kube-vip.yaml", "/etc/kubernetes/manifests/kube-vip.yaml")
            # and restart kubelet
            systemctl_restart_kubelet(node["nodeIp"], node["sshUser"], node["sshKey"])
        # endif    

        # occasionally the node-health check is too fast - so we have to defer it a little bit
        print("Checking node health (~15s graceperiod)...")
        sleep(15)
        
        # the init-master may not get ready until the network-cni is installed and up!
        while not check_node_health(node["nodeIp"], node["sshUser"], node["sshKey"], node["nodeName"], is_init_master=True):
            print("Node not ready yet, waiting 5 seconds...")
            sleep(5)
        # endwhile

        # apply network-cni we will use the tigera-operator and apply the needed custom-resource.yaml that will be generated from the templates
        # original: kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/tigera-operator.yaml
        # original: kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/{{ calico.tigera.version }}/manifests/tigera-operator.yaml

        # in case of 3.30+ we need to download 2 files (operator-crds.yaml and tigera-operator.yaml)
        calico_version_string = cluster_values["calico"]["tigera"]["version"]

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
            kubectl_install_manifest(node["nodeIp"], node["sshUser"], node["sshKey"], "/root/operator-crds.yaml", create=True)
        # endif

        # we have to download it locally and edit a line
        # download_file(node["nodeIp"], node["sshUser"], node["sshKey"], "https://raw.githubusercontent.com/projectcalico/calico/" + cluster_values["calico"]["tigera"]["version"] + "/manifests/tigera-operator.yaml")
        print("Downloading tigera-operator.yaml...")

        # wget_return_code = subprocess.call("wget -q -P " + config_path + " https://raw.githubusercontent.com/projectcalico/calico/" + cluster_values["calico"]["tigera"]["version"] + "/manifests/tigera-operator.yaml -O tigera-operator.yaml", shell=True)
        # curl -L -o ./clusters/test-cluster/node-1.home.arpa/tigera-operator.yaml https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/tigera-operator.yaml
        curl_return_code = subprocess.call("curl -s -L -o " + config_path + "/tigera-operator.yaml https://raw.githubusercontent.com/projectcalico/calico/" + cluster_values["calico"]["tigera"]["version"] + "/manifests/tigera-operator.yaml", shell=True)
        if curl_return_code == 0:
            print("Downloaded tigera-operator.yaml successfully")
        else:
            print("Downloaded tigera-operator.yaml failed")
            sys.exit(1)
        # endif
        inplace_file_change(config_path + "/tigera-operator.yaml", "image: quay.io/", "image: " + cluster_values["calico"]["tigera"]["registry"])
        upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/tigera-operator.yaml", "/root/tigera-operator.yaml")
        # we apply/create the downloaded tigera-operator
        kubectl_install_manifest(node["nodeIp"], node["sshUser"], node["sshKey"], "/root/tigera-operator.yaml", create=True)
        # we apply the previously uploaded custom-resource.yaml for tigera
        kubectl_install_manifest(node["nodeIp"], node["sshUser"], node["sshKey"], "/root/calico_tigera_custom_resource.yaml", create=True)

        # occasionally the node-health check is too fast - so we have to defer it a little bit
        print("Checking node health (~15s graceperiod)...")
        sleep(15)
        
        # we check again until node gets ready - after installing the network-cni this should work
        while not check_node_health(init_master_ip, init_master_user, init_master_key, node["nodeName"]):
            print("Node not ready yet, waiting 5 seconds...")
            sleep(5)
        # endwhile

        # create .kube-folder in /root and link .kube/config to /etc/kubernetes/admin.conf and enable bash-completion
        enable_kubectl(node["nodeIp"], node["sshUser"], node["sshKey"])

        if node["type"] == "init-master+worker":
            print("Untainting node...")
            kubectl_untaint_node(init_master_ip, init_master_user, init_master_key, node["nodeName"])
            print("Setting worker node role label...")
            kubectl_label_node(init_master_ip, init_master_user, init_master_key, node["nodeName"], node_labels=["node-role.kubernetes.io/worker="])
        # endif
        
        # add additional node labels if there are any configured
        if "nodeLabels" in node:
            print("Setting additional node-labels...")
            kubectl_label_node(init_master_ip, init_master_user, init_master_key, node["nodeName"], node_labels=node["nodeLabels"])
        # endif
    # endif
    print("########## END Node-Setup for " + node["nodeName"] + " ##########")

    for node in cluster_values["nodes"]:
        # create init-master-config.yaml - also we need a special kube-vip.yaml for the init-master - keyword "super-admin.conf"
        if "init-master" == node["type"] or "init-master+worker" == node["type"]:
            # we skip this because it was set up before
            print("Skipping init-master because it was already set up. Continuing with next node...")
            continue
        else:
            print("######### BEGIN Node-Setup for " + node["nodeName"] + " #########")
            print("######### Node will be added as type: " + node["type"])
            config_path = cluster_files_dir + "/../clusters/" + cluster_values["cluster"]["name"] + "/" + node["nodeName"]
            check_config_path = os.path.exists(config_path)

            if not check_config_path:
                os.makedirs(config_path)
            # endif
        # endif
        
        if init_master_ip is not None:
            # create join-master-config.yaml
            if "master" == node["type"] or "master+worker" == node["type"]:
                # we temporarily add the node's interface to the main cluster_values so we can render the static-pod-manifest for kube-vip
                cluster_values["nodeInterface"] = node["nodeInterface"]

                # render kube-vip manifest
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

                # check if kubeadm kubelet kubectl kubernetes-cni containerd.io is installed and install them if needed and confirmed
                check_and_install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], unattended=unattended_flag)
                check_swap(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_sysctl(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_modprobe(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_containerd(node["nodeIp"], node["sshUser"], node["sshKey"])

                # check if node is not already part of a cluster
                check_is_in_cluster(init_master_ip, init_master_user, init_master_key, node, unattended=unattended_flag)
                
                # we fix a possible issue with the sandbox_image parameter in the containerd's config.toml
                kubeadm_images = kubeadm_get_images_list(node["nodeIp"], node["sshUser"], node["sshKey"])
                print("Trying to fix containerd's config.toml")
                fix_containerd_config(node["nodeIp"], node["sshUser"], node["sshKey"], kubeadm_images, cluster_values["kube"]["image"]["repository"], config_path)
                
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
                upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/audit-policy.yaml", "/etc/kubernetes/audit-policy.yaml")
                upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/kube-vip.yaml", "/etc/kubernetes/manifests/kube-vip.yaml")
                upload_file(node["nodeIp"], node["sshUser"], node["sshKey"], config_path + "/join-master-config.yaml", "/root/join-master-config.yaml")

                # execute kubeadm-command for joining a master node
                kubadm_join_cluster(node["nodeIp"], node["sshUser"], node["sshKey"], "/root/join-master-config.yaml")

                # occasionally the node-health check is too fast - so we have to defer it a little bit
                print("Checking node health (~15s graceperiod)...")
                sleep(15)
                
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
            # endif

            if "worker" == node["type"]:
                # we need: kube.token, kube.cert.hash, nodeName, nodeIp
                # we temporarily add those values to the main cluster_values so we can render the static-pod-manifest for kube-vip
                cluster_values["nodeName"] = node["nodeName"]
                cluster_values["nodeIp"] = node["nodeIp"]
                (kube_token, kube_cert_hash) = kubeadm_get_kube_token_and_hash(init_master_ip, init_master_user, init_master_key)
                cluster_values["kube"]["token"] = kube_token
                cluster_values["kube"]["cert"] = {}
                cluster_values["kube"]["cert"]["hash"] = kube_cert_hash

                render_file(config_path + "/join-worker-config.yaml", join_worker_config, cluster_values)

                # check if kubeadm kubelet kubectl kubernetes-cni containerd.io is installed and install them if needed and confirmed
                check_and_install_packages(node["nodeIp"], node["sshUser"], node["sshKey"], cluster_values["kube"]["version"], unattended=unattended_flag)
                check_swap(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_sysctl(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_modprobe(node["nodeIp"], node["sshUser"], node["sshKey"])
                check_containerd(node["nodeIp"], node["sshUser"], node["sshKey"])

                # check if node is not already part of a cluster
                check_is_in_cluster(init_master_ip, init_master_user, init_master_key, node, unattended=unattended_flag)

                # we fix a possible issue with the sandbox_image parameter in the containerd's config.toml
                kubeadm_images = kubeadm_get_images_list(node["nodeIp"], node["sshUser"], node["sshKey"])
                print("Trying to fix containerd's config.toml")
                fix_containerd_config(node["nodeIp"], node["sshUser"], node["sshKey"], kubeadm_images, cluster_values["kube"]["image"]["repository"], config_path)

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

                # occasionally the node-health check is too fast - so we have to defer it a little bit
                print("Checking node health (~15s graceperiod)...")
                sleep(15)
                
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
            # endif
        # endif
        print("########## END Node-Setup for " + node["nodeName"] + " ##########")
    # endfor

    print("########## END Cluster-Setup for " + cluster_values["cluster"]["name"] + " ##########")
    cluster_create_success = True
    
    return cluster_create_success
# enddef
