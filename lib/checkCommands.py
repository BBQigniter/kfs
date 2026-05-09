#!/usr/bin/env python
import json

import paramiko
import sys
from subprocess import run
from time import sleep
from lib.kfsTools import install_packages, dnf5_used, stream_output
from lib.kubectlCommands import kubectl_get_cluster_name
from schema import Schema, SchemaError, Optional, Regex, Or


def check_node_health(init_master_ip, user, key, node_name, is_init_master=False, is_upgrade=False):
    """
    Checks set node to be "Ready"
    
    :param init_master_ip: str
    :param user: str
    :param key: file
    :param node_name: str
    :param is_init_master: bool
    :param is_upgrade: bool
    :return: bool
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)
    
    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes " + node_name)
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)
    
    # we found something we need to check more
    if stdout.channel.recv_exit_status() == 0:
        for line in cmd_output:
            name, status, roles, age, version = line.split()
            #print(name, status, roles, age, version)
            # we check if it's part of a cluser - for upgrades we have to do additional checks like:
            # * get the cluster-name it is part of so that we do not upgrade the wrong node
            # * is the version step allowed as upgrades must not be larger than one minor version higher (e.g. 1.28 -> 1.29)
            if node_name in name and status == "Ready":
                print("Node seems to be ready.")
                client.close()
                return True
            elif node_name in name and status == "Ready,SchedulingDisabled" and is_upgrade:
                # means node is correctly drained
                print("Node seems to be ready.")
                client.close()
                return True
            elif node_name in name and status != "Ready" and is_init_master:
                # means we can access the API to install the network-cni
                print("Init-master OK, we can access the API.")
                client.close()
                return True
            elif node_name in name and status != "Ready":
                client.close()
                return False
            # endif
        # endfor
    # endif
    
    client.close()
# enddef


def check_file_exists(node_ip, user, key, filepath):
    """
    Simply checks if file exists on node
    
    :param node_ip: str
    :param user: str
    :param key: file
    :param filepath: str
    :return: bool
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    
    stdin, stdout, stderr = client.exec_command("test -e " + filepath)
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)
    
    if stdout.channel.recv_exit_status() == 0:
        print("File " + filepath + " exists")
        client.close()
        return True
    else:
        print("File " + filepath + " does NOT exist")
        client.close()
        return False
    # endif
# enddef


def check_port_listening(node_ip, user, key, port):
    """
    Checks if given port is listening to something
    
    :param node_ip: str
    :param user: str
    :param key: file
    :param port: int
    :return: bool
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    
    port = str(port)
    
    stdin, stdout, stderr = client.exec_command("ss -tulwn | grep LISTEN | grep " + port)
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)
    
    if stdout.channel.recv_exit_status() == 0:
        print("Something is listening on port " + port)
        client.close()
        return True
    else:
        print("Nothing is listening on port " + port)
        client.close()
        return False
    # endif
# enddef


# TODO: still needs improvements this is too ugly
def check_is_in_cluster(init_master_ip, user, key, node_info, is_upgrade=False, unattended=False, cluster_name_info=None):
    """
    Checks if given node is part of selected cluster
    
    :param init_master_ip: str
    :param user: str
    :param key: file
    :param node_info: list 
    :param is_upgrade: boold
    :param unattended: bool
    :param cluster_name_info: list
    """
    #  * Check for port 10250 - good indicator that the node is part of a cluster (`ss -tulwn | grep LISTEN | grep 10250`)
    #    * Problem: kubernetes might not be started
    #  * Check for /etc/kubernetes/kubelet.conf (`test -e /etc/kubernetes/kubelet.conf`)
    #    * Problem: might be saved somewhere else
    #  * Check for /etc/kubernetes/admin.conf - might indicate that the node a master node (`test -e /etc/kubernetes/admin.conf`)
    #    * Check further to get cluster-name to get an indicator (`kubectl config view --minify -o jsonpath='{.clusters[].name}'`)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)
    
    node_not_part_of_cluster = False
    
    # if this is not an upgrade we have to do several different checks to make sure the nodes are not already part of a cluster
    if not is_upgrade:
        if init_master_ip == node_info["nodeIp"]:
            print("No need to connect to other node...")
            # we want to prevent short-circuiting so instead of
            # if check_file_exists(init_master_ip, user, key, "/etc/kubernetes/kubelet.conf") or check_port_listening(init_master_ip, user, key, 10250):
            # we use
            if any([check_file_exists(init_master_ip, user, key, "/etc/kubernetes/kubelet.conf"), check_port_listening(init_master_ip, user, key, 10250)]):
                if check_file_exists(init_master_ip, user, key, "/etc/kubernetes/admin.conf"):
                    print("Checking if this node is already some master node of some cluster")
                    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes")
                    cmd_output, cmd_errors = stream_output(stdout)
                    # cmd_output, cmd_errors = output_to_list(stdout, stderr)
            
                    # we found something we need to check more
                    if stdout.channel.recv_exit_status() == 0:
                        for line in cmd_output:
                            name, status, roles, age, version = line.split()
                            # we check if it's part of a cluser - for upgrades we have to do additional checks like:
                            # * get the cluster-name it is part of, so that we do not upgrade the wrong node
                            if node_info["nodeName"] in name:
                                cluster_name = kubectl_get_cluster_name(init_master_ip, user, key)
                                print("Node " + node_info["nodeName"] + " is part of a cluster with name " + cluster_name + " and this is NOT an upgrade! Aborting...")
                                client.close()
                                sys.exit(2)
                            # endif
                        # endfor
                    # endif
                else:
                    print("Node most probably IS part of some other cluster! Aborting...")
                    client.close()
                    sys.exit(2)
                # endif
            else:
                print("Node most probably not part of any cluster.")
                node_not_part_of_cluster = True
            # endif
        else:
            # we first check if node is already part of this cluster
            print("Checking if this node is already part of this cluster")
            stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes")
            cmd_output, cmd_errors = stream_output(stdout)
            # cmd_output, cmd_errors = output_to_list(stdout, stderr)
            
            # we found something we need to check more
            if stdout.channel.recv_exit_status() == 0:
                for line in cmd_output:
                    name, status, roles, age, version = line.split()
                    # we check if it's part of a cluser - for upgrades we have to do additional checks like:
                    # * get the cluster-name it is part of, so that we do not upgrade the wrong node
                    if node_info["nodeName"] in name:
                        cluster_name = kubectl_get_cluster_name(init_master_ip, user, key)
                        print("Node " + node_info["nodeName"] + " is part of this cluster with name " + cluster_name + " but this is NOT an upgrade! Aborting...")
                        client.close()
                        sys.exit(2)
                    else:
                        node_not_part_of_cluster = True
                    # endif
                # endfor
            # endif
            
            # now we do some additional checks by connecting directly to the node
            # we want to prevent short-circuiting so instead of
            # if check_file_exists(node_info["nodeIp"], node_info["sshUser"], node_info["sshKey"], "/etc/kubernetes/kubelet.conf") or check_port_listening(node_info["nodeIp"], node_info["sshUser"], node_info["sshKey"], 10250):
            # we use
            if any([check_file_exists(node_info["nodeIp"], node_info["sshUser"], node_info["sshKey"], "/etc/kubernetes/kubelet.conf"), check_port_listening(node_info["nodeIp"], node_info["sshUser"], node_info["sshKey"], 10250)]):
                if check_file_exists(node_info["nodeIp"], node_info["sshUser"], node_info["sshKey"], "/etc/kubernetes/admin.conf"):
                    cluster_name = kubectl_get_cluster_name(node_info["nodeIp"], node_info["sshUser"], node_info["sshKey"])
                    print("Node" + node_info["nodeName"] + " is part of a cluster with name " + cluster_name + "! Aborting...")
                    client.close()
                    sys.exit(2)
                else:
                    print("Node most probably IS part of some other cluster! Aborting...")
                    client.close()
                    sys.exit(2)
                # endif
            else:
                print("Node most probably not part of any cluster.")
                node_not_part_of_cluster = True
            # endif
        # endif
    else:  # if this is an upgrade, we need to check if the node is really part of the cluster (not that it was mistakenly added to the config and not correctly set up)
        print("Checking if this node is already part of this cluster")
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes")
        cmd_output, cmd_errors = stream_output(stdout)
        # cmd_output, cmd_errors = output_to_list(stdout, stderr)
        
        if stdout.channel.recv_exit_status() == 0:
            for line in cmd_output:
                name, status, roles, age, version = line.split()
                # we check if it's part of a cluser - for upgrades we have to do additional checks like:
                # * get the cluster-name it is part of, so that we do not upgrade the wrong node
                if node_info["nodeName"] in name:
                    cluster_name = kubectl_get_cluster_name(init_master_ip, user, key)
                    if cluster_name == cluster_name_info:
                        print("Node " + node_info["nodeName"] + " is part of this cluster with name " + cluster_name)
                    else:
                        print("Node most probably IS part of some other cluster! Aborting...")
                        client.close()
                        sys.exit(2)
                    # endif
                # endif
            # endfor
        # endif
    # endif
    
    if node_not_part_of_cluster and not is_upgrade:
        if unattended:
            print("It looks like the node is NOT part of a cluster. Unattended flag set, continuing...")
        else:
            user_input = input("It looks like the node is NOT part of a cluster. Continue? (yes/no): ")
            if user_input.lower() in ["yes", "y"]:
                print("Proceeding with creating a cluster...")
            else:
                print("Exiting - please, check node!")
                client.close()
                sys.exit(2)
            # endif
        # endif
    else:
        # TODO: check what happend, this could be missleading - I had a case where this message was shown, when creating a cluster and the setup was canceled in-between
        print("Node looking ok for being upgraded, continuing...")
    # endif
    
    client.close()
# enddef


def check_and_install_packages(node_ip, user, key, kube_version, is_upgrade=False, unattended=False):
    """
    Checks if needed packages are installed.
    
    :param node_ip: str
    :param user: str
    :param key: file
    :param kube_version: str
    :param is_upgrade: bool
    :param unattended: bool
    """
    
    # workflow
    # * check if packages are installed
    # * check which version of kubeadm, kubelet and kubectl is installed
    # * if nothing installed, ask if they should be installed
    # * if different version is installed, and it's not an upgrade - exit
    
    # we need to check if any kubernetes stuff is not already installed
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    
    needed_packages = ("kubeadm", "kubelet", "kubectl", "containerd.io")  # we definitely need those - dependencies like kubernetes-cni, cri-tools will be included automatically
    additional_packages_for_updates = ("cri-tools", "kubernetes-cni")  # in case the upgrade-cluster was called, we also might need to upgrade these packages
    same_version_packages = ("kubeadm", "kubelet", "kubectl")  # the need to be the same version
    
    # we check if any of the given packages is installed
    # TODO: find better way to do this - it's a little bit slow
    not_found = []
    installed = []
    need_install_packages = []
    start_installation = False
    version_missmatch = False
    need_install = False
    
    for package in needed_packages:
        if dnf5_used(node_ip, user, key):
            stdin, stdout, stderr = client.exec_command("dnf list --installed --setopt=disable_excludes=* | grep " + package + " || >&2 echo '" + package + " not found'")
            cmd_output, cmd_errors = stream_output(stdout)
            # cmd_output, cmd_errors = output_to_list(stdout, stderr)
        else:
            stdin, stdout, stderr = client.exec_command("dnf list installed --disableexcludes=all | grep " + package + " || >&2 echo '" + package + " not found'")
            cmd_output, cmd_errors = stream_output(stdout)
            # cmd_output, cmd_errors = output_to_list(stdout, stderr)
        #for line in iter(stdout.readline, ""):
        for line in cmd_output:
            if package in line:
                installed.append(line.strip())
        #for line in iter(stderr.readline, ""):
        for line in cmd_errors:
            if package in line:
                not_found.append(line.strip())
    # endfor

    if len(installed) > 0:
        # we now loop through the found installed packages
        for package in installed:
            package_name, package_version, package_repo = package.split()
            #print(package_name)
            # TODO: find better method - we heavily rely on packages keeping to the "RPM Packages Naming Convention" which could lead to troubles
            #  The naming convention for RPM packages is name-version-release.architecture.rpm
            #  And here the package_name is name-version-release.architecture
            package_name, package_arch = package_name.rsplit(".", 1)
            
            # we check if "kubeadm", "kubelet", "kubectl" have the same version installed
            if (kube_version in package_version) and any(same_version_package in package_name for same_version_package in same_version_packages) and not is_upgrade:
                print(package_name + " has already correct version installed: " + package_version)
            # if we see that "kubeadm", "kubelet", "kubectl" packages have a different versions than configured, and it's NOT an upgrade, we indicate a version-missmatch that has to be resolved
            elif (kube_version not in package_version) and any(same_version_package in package_name for same_version_package in same_version_packages) and not is_upgrade:
                print(package_name + " has missmatching version installed: " + package_version)
                version_missmatch = True
            # if we see that "kubeadm", "kubelet", "kubectl" packages have a different versions than configured, and it's an upgrade, we indicate that those packages will be upgraded
            elif (kube_version not in package_version) and any(same_version_package in package_name for same_version_package in same_version_packages) and is_upgrade:
                print(package_name + " upgrade needed, installed version: " + package_version + " - needed version: " + kube_version)
                version_missmatch = True
                # if it's an upgrade we add the package to the install-list
                need_install_packages.append(package_name + "-" + kube_version)
            else:
                # list all other installed needed packages with their currently installed version
                print(package_name + " version installed: " + package_version)
            # endif
        # endfor
    # endif
    
    # we get a nice list of packages that were not found at all
    if len(not_found) > 0:
        need_install = True
        for package in not_found:
            package_name, not_found_string = package.split(" ", 1)
            if any(same_version_package in package_name for same_version_package in same_version_packages):
                need_install_packages.append(package_name + "-" + kube_version)
            else:
                need_install_packages.append(package_name)
        # we must inform user to fix this
    # endif
    
    # then we check if installation is allowed and try to install packages
    if len(need_install_packages) > 0 and not is_upgrade and not version_missmatch and need_install:
        print("Packages missing, starting installation...")
        start_installation = True
    elif is_upgrade and version_missmatch:
        print("Upgrading packages...")
        start_installation = True
    elif len(need_install_packages) == 0 and not version_missmatch and not need_install:
        print("All packages needed are installed and have the selected version! Continuing...")
    else:
        if version_missmatch:
            print("Version missmatch detected!")
        print("Not installing packages! Was the correct version configured in the cluster-YAML file? Please, check! Exiting...")
        client.close()
        sys.exit(2)
    # endif
    
    if start_installation:
        # if it's an upgrade we need to add additional packages that might have an update available
        if is_upgrade:
            need_install_packages.extend(additional_packages_for_updates)
            auto_install_type_message = "Autoupgrading"
            manual_install_type_message = "upgrade"
        else:
            auto_install_type_message = "Autoinstalling"
            manual_install_type_message = "install"
        # endif

        print("Packages to be considered: " + (" ").join(need_install_packages))
        if unattended:
            print(auto_install_type_message + " packages as unattended flag set, continuing...")
            install_packages(node_ip, user, key, need_install_packages, unattended, kubernetes_version=kube_version)
        else:
            user_input = input("Do you want to " + manual_install_type_message + " them and the dependencies now? (yes/no): ")
            if user_input.lower() in ["yes", "y"]:
                print("Trying to " + manual_install_type_message + " packages...")
                install_packages(node_ip, user, key, need_install_packages, kubernetes_version=kube_version)
            else:
                print("Aborting - please, check prerequisites to be handled first before continuing...")
                client.close()
                sys.exit(2)
            # endif
        # endif
    # endif
    
    client.close()
# enddef


def check_upgrade_scripts(node_info, cluster_files_dir):
    """
    A simple function for calling the upgradeCheckScripts if there are any defined in the cluster-file
    
    :param node_info: dict
    :param cluster_files_dir: str 
    """
    if "upgradeCheckScripts" in node_info:
        print("Upgrade Check Scripts found - Checking if they exit correctly to continue...")
        for script in node_info["upgradeCheckScripts"]:
            run_command = [cluster_files_dir + "/../additional_scripts/" + script["scriptPath"]]
            # TODO: this might need adaptions, we probably also have to split parameters in format "--argument value" into a list before
            if "scriptParameters" in script:
                for parameter in script["scriptParameters"]:
                    run_command.append(parameter)
                # endfor
            # endif
            
            while run(run_command).returncode != 0:
                print("Script for checking prerequisite to continue failed, checking again in 5 seconds...")
                sleep(5)
            # endwhile
            
            print("Script " + cluster_files_dir + "../additional_scripts/" + script["scriptPath"] + " executed successfully!")
        # endfor
    else:
        print("No upgrade-check-scripts defined, continuing...")
    # endif
# enddef


def check_cluster_file_syntax(cluster_file):
    """
    Read given cluster-file and checks if syntax is correct - it's kept very simple some cases might be missed
    Must be adapted if additional keys are needed in some sections!
    
    :param cluster_file: file
    """
    # as described in this howto https://www.andrewvillazon.com/validate-yaml-python-schema/
    
    ip4_regex = r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
    ip4_with_cidr_regex = r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}/\d+$"
    gc_regex = r"^(normal|early)$"
    cluster_file_schema = Schema(
        {
            "cluster": {
                "name": str
            },
            "dnf-repo": {
                "upload": bool
            },
            "kube": {
                "version": str,
                "image": {
                    "repository": str
                },
                "api": {
                    "vip": Regex(ip4_regex),
                    "port": str
                },
                "service": {
                    "subnet": Regex(ip4_with_cidr_regex)
                },
                "pod": {
                    "subnet": Regex(ip4_with_cidr_regex)
                }
            },
            "calico": {
                "tigera": {
                    "version": str,
                    "registry": str,
                    "blockSize": str
                },
                "kubelet": {
                    "extraArgs": {
                        "maxPods": str
                    }
                }
            },
            "kubeVip": {
                "image": {
                    "path": str
                }
            },
            "nodes": [
                {
                    "nodeName": str,
                    "nodeIp": Regex(ip4_regex),
                    "nodeInterface": str,
                    "type": Or("init-master", "master", "master+worker", "init-master+worker", "worker"),
                    "sshUser": str,
                    "sshKey": str,
                    Optional("nodeLabels"): list,
                    Optional("upgradeCheckScripts"): [
                        {
                            Optional("scriptDescription"): str,
                            "scriptPath": str,
                            Optional("scriptParameters"): list
                        }
                    ],
                    Optional("kubelet"): {
                        Optional("garbageCollection"): Regex(gc_regex)
                    }
                }
            ]
        }
    )
    
    try:
        cluster_file_schema.validate(cluster_file)
        print("Configuration is valid.")
    except SchemaError as se:
        for error in se.errors:
            if error:
                print(error)
        for error in se.autos:
            if error:
                print(error)
        
        print("Please check the cluster-file schema, and errors! Aborting...")
        sys.exit(1)
    # endtry
# enddef


def check_workload(init_master_ip, user, key, workload_type, workload_namespace, workload_name=None, image_version_tag=None):
    """
    Function to check given workloads if they are ok. If image_version_tag is defined it also can check if an update works as intended.
    With image_version_tag is currently only supported if also sidecars have the same version-tag
    
    :param init_master_ip: str
    :param user: str
    :param key: file
    :param workload_type: str - can be multiple types - currently supported for example "deployments,daemonsets"
    :param workload_namespace: str
    :param workload_name: str
    :param image_version_tag: str
    
    :return: bool
    """
    
    def check_deployment_replicas(deployment_item, update_missing=False):
        """
        A function for checking if a deployment workload is healthy
        
        :param deployment_item: dict
        :return: bool
        """
        deployment_healthy = False
        
        if "readyReplicas" in deployment_item["status"]:
            if deployment_item["status"]["readyReplicas"] == deployment_item["status"]["updatedReplicas"] == deployment_item["status"]["replicas"]:
                message = "Workload " + deployment_item["metadata"]["name"] + " ready and healthy"
                if update_missing:
                    message = message + " but version not yet updated"
                
                print(message)
                
                deployment_healthy = True
            else:
                print("Workload " + deployment_item["metadata"]["name"] + " NOT yet ready and healthy")
            # endif
        else:
            print("Workload " + deployment_item["metadata"]["name"] + "  NOT yet ready and healthy")
        # endif
        
        return deployment_healthy
    # enddef

    def check_daemonset_replicas(daemonset_item, update_missing=False):
        """
        A function for checking if a daemonset workload is healthy
        
        :param daemonset_item: dict
        :return: bool
        """
        daemonset_healthy = False

        if daemonset_item["status"]["numberReady"] == \
           daemonset_item["status"]["desiredNumberScheduled"] == \
           daemonset_item["status"]["numberAvailable"] == \
           daemonset_item["status"]["currentNumberScheduled"] == \
           daemonset_item["status"]["desiredNumberScheduled"] == \
           daemonset_item["status"]["updatedNumberScheduled"]:
            message = "Workload " + daemonset_item["metadata"]["name"] + " ready and healthy"
            
            if update_missing:
                message = message + " but version not yet updated"
            
            print(message)
            
            daemonset_healthy = True
        else:
            print("Workload " + daemonset_item["metadata"]["name"] + " NOT yet ready and healthy")
        # endif
        
        return daemonset_healthy
    # enddef
    
    def check_item_kind(workload_item):
        """
        A function for checking which type of workload must be further checked for healthiness
        
        :param workload_item: dict
        :return: bool
        """
        workload_healthy = []
        update_missing = False
        
        if workload_item["kind"] == "Deployment":
            if image_version_tag is not None:
                for container in workload_item["spec"]["template"]["spec"]["containers"]:
                    # TODO: find solution: this can be problematic if different version/image-paths are used for multiple containers
                    if image_version_tag in container["image"]:
                        print("Image Version Tag " + image_version_tag + " found in image-path " + container["image"])
                        workload_healthy.append(True)
                    else:
                        print("Image Version Tag " + image_version_tag + " NOT found in image-path " + container["image"])
                        workload_healthy.append(False)
                        update_missing = True
                    # endif
                # endfor

                if check_deployment_replicas(workload_item, update_missing):
                    workload_healthy.append(True)
                else:
                    workload_healthy.append(False)
                # endif
            else:
                # we only check for the status workload
                if check_deployment_replicas(workload_item):
                    workload_healthy.append(True)
                else:
                    workload_healthy.append(False)
                # endif
            # endif
        elif workload_item["kind"] == "DaemonSet":
            if image_version_tag is not None:
                for container in workload_item["spec"]["template"]["spec"]["containers"]:
                    # TODO: find solution: this can be problematic if different version/image-paths are used for multiple containers
                    if image_version_tag in container["image"]:
                        print("Image Version Tag " + image_version_tag + " found in image-path " + container["image"])
                        workload_healthy.append(True)
                    else:
                        print("Image Version Tag " + image_version_tag + " NOT found in image-path " + container["image"])
                        workload_healthy.append(False)
                        update_missing = True
                    # endif
                # endfor

                if check_daemonset_replicas(workload_item, update_missing):
                    workload_healthy.append(True)
                else:
                    workload_healthy.append(False)
                # endif
            else:
                # we only check for the status workload
                if check_daemonset_replicas(workload_item):
                    workload_healthy.append(True)
                else:
                    workload_healthy.append(False)
                # endif
            # endif
        else:
            print("only DaemonSet and Deployments supported")
            workload_healthy.append(False)
        # endif
        
        #print(workload_healthy)
        if all(workload_healthy):
            return True
        else:
            return False
    # enddef
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)
    
    if workload_name is None:
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=/etc/kubernetes/admin.conf get " + workload_type + " -n " + workload_namespace + " -o json")
    else:
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=/etc/kubernetes/admin.conf get " + workload_type + " " + workload_name + " -n " + workload_namespace + " -o json")
    # endif

    cmd_output, cmd_errors = stream_output(stdout)
    #cmd_output, cmd_errors = output_to_list(stdout, stderr))
    
    # we found something we need to check more
    if stdout.channel.recv_exit_status() == 0:
        # we convert the output to json
        output_to_string = "\n".join(cmd_output)
        output_to_json = json.loads(output_to_string)

        workload_healthy = []
        
        # we need to check if we found multiple items or if we just have on single workload output
        # TODO: probably can be done a lot better because stuff from single and multiple workloads is similar
        if "items" in output_to_json:
            for item in output_to_json["items"]:
                if check_item_kind(item):
                    workload_healthy.append(True)
                else:
                    workload_healthy.append(False)
            # endfor
        else:
            if check_item_kind(output_to_json):
                workload_healthy.append(True)
            else:
                workload_healthy.append(False)
        # endif
        
        client.close()
        
        if all(workload_healthy):
            return True
        else:
            return False
        # endif
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def check_swap(node_ip, user, key):
    """
    Connects to node and checks if swap is enabled
    
    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    print("Checking if swap is disabled...")
    stdin, stdout, stderr = client.exec_command("swapon -s")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)
    
    if stdout.channel.recv_exit_status() == 0:
        #print(len(cmd_output))
        #print(cmd_output)
        if len(cmd_output) > 0:
            print("Please, permanently disable 'swap' first on the node!")
            user_input = input("Continue? (yes/no): ")
            if user_input.lower() in ["yes", "y"]:
                print("Continuing...")
            else:
                print("Exiting - please, investigate on node...")
                client.close()
                sys.exit(2)
            # endif
        # endif
    # endif
    
    client.close()
# enddef


def check_sysctl(node_ip, user, key):
    """
    Connects to node and checks needed sysctl parameter is set

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    print("Checking needed sysctl-parameter(s) (at least net.ipv4.ip_forward must be set) ...")
    stdin, stdout, stderr = client.exec_command("sysctl net.ipv4.ip_forward | grep 1")
    cmd_output, cmd_errors = stream_output(stdout)
    #cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        if len(cmd_output) == 0:
            print("Please, permanently enable the sysctl-parameter 'net.ipv4.ip_forward' on the node before continuing!")
            user_input = input("Continue? (yes/no): ")
            if user_input.lower() in ["yes", "y"]:
                print("Continuing...")
            else:
                print("Exiting - please, investigate on node...")
                client.close()
                sys.exit(2)
            # endif
        # endif
    # endif
    client.close()
# enddef


def check_modprobe(node_ip, user, key):
    """
    Connects to node and checks needed kernel modules
    
    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    print("Checking for needed kernel modules (overlay and br_netfilter)...")
    stdin, stdout, stderr = client.exec_command("lsmod | awk '{print $1}' | grep -E '(overlay|br_netfilter)'")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        if len(cmd_output) < 2:
            print("Please, permanently configure to load the kernel modules 'overlay' and 'br_netfilter' on the node before continuing!")
            user_input = input("Continue? (yes/no): ")
            if user_input.lower() in ["yes", "y"]:
                print("Continuing...")
            else:
                print("Exiting - please, investigate on node...")
                client.close()
                sys.exit(2)
            # endif
        # endif
    # endif
    client.close()
# enddef


def check_containerd(node_ip, user, key):
    """
    Connects to node and checks if containerd's config.toml file is strangely small
    
    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    print("Checking containerd-config.toml (should have more than ~200 lines)...")
    stdin, stdout, stderr = client.exec_command("wc -l /etc/containerd/config.toml | awk '{print $1}'")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)
    
    if stdout.channel.recv_exit_status() == 0:
        try:
            if int(cmd_output[0]) < 200:
                print("Please, check your '/etc/containerd/config.toml' file on the node before continuing!")
                print("Hint: You might want to create a backup of the current config and execute: `containerd config default > /etc/containerd/config.toml`")
                user_input = input("Continue? (yes/no): ")
                if user_input.lower() in ["yes", "y"]:
                    print("Continuing...")
                else:
                    print("Exiting - please, check your containerd-config on the node...")
                    client.close()
                    sys.exit(2)
                # endif
        except:
            print("Exiting - please, check your containerd-config on the node...")
            client.close()
            sys.exit(2)
        # endtry
    # endif
    
    client.close()
# enddef
