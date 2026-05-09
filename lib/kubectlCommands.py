#!/usr/bin/env python

import paramiko
import sys
from lib.kfsTools import stream_output


def enable_kubectl(node_ip, user, key):
    """
    Simply enables bash-completion for kubectl and configures for root-user to use simple kubectl commands.
    Only used on master-nodes!

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    commands = ["mkdir -p /root/.kube", "ln -sf /etc/kubernetes/admin.conf /root/.kube/config", "kubectl completion bash | tee /etc/bash_completion.d/kubectl > /dev/null"]

    for cmd in commands:
        stdin, stdout, stderr = client.exec_command(cmd)
        cmd_output, cmd_errors = stream_output(stdout)
        # cmd_output, cmd_errors = output_to_list(stdout, stderr)

        if stdout.channel.recv_exit_status() == 0:
            print("Executed successfully command: " + cmd)
        else:
            print("Something went wrong, please check!")
            print("\n".join(cmd_output))
            print("\n".join(cmd_errors))
            client.close()
            sys.exit(2)
        # endif
    # endfor

    client.close()
# enddef


def kubectl_untaint_node(init_master_ip, user, key, node_name, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Simply removes the control-plane taint from given master-node

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param node_name: str
    :param kubeconfig: str
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    # kubectl taint node node-1.home.arpa node-role.kubernetes.io/control-plane-
    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " taint node " + node_name + " node-role.kubernetes.io/control-plane-")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        for line in cmd_output:
            if "node/" + node_name + " untainted" in line:
                print("Node " + node_name + " untainted")
    else:
        print("WARNING - unable to untaint node")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        print("Continuing because this is not severe, please check later!")
    # endif

    client.close()
# enddef


def kubectl_label_node(init_master_ip, user, key, node_name, node_labels: list, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Label given nodes with labels read from a list

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param node_name: str
    :param node_labels: list
    :param kubeconfig: str
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    node_labels_string = " ".join(node_labels)

    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " label node " + node_name + " " + node_labels_string)
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        for line in cmd_output:
            if "node/" + node_name + " labeled" in line:
                print("Node " + node_name + " labeled")
    else:
        print("WARNING - unable to label node")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        print("Continuing because this is not severe, please check later!")
    # endif

    client.close()
# enddef


def kubectl_install_manifest(init_master_ip, user, key, manifest_path, create=False, replace=False, server_side=False, force_conflicts=False, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Applies, creates or replaces given manifest file on cluster

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param manifest_path: file
    :param create: bool
    :param replace: bool
    :param server_side: bool
    :param force_conflicts: bool
    :param kubeconfig: str
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    stdin, stdout, stderr = None, None, None  # to get rid of some pycharm PEP-warnings

    # TODO: this has to be accomplished a lot smarter - no brain now for this
    if not create and not replace and not server_side and not force_conflicts:
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " apply  -f " + manifest_path)
        
    if create and not replace and not server_side and not force_conflicts:
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " create -f " + manifest_path)
        
    if server_side and force_conflicts and not create and not replace:
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " apply --server-side --force-conflicts -f " + manifest_path)
        
    if not create and replace and not server_side and not force_conflicts:
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " replace -f " + manifest_path)
    elif create and replace:
        print("Wrong kubectl_apply_manifest command")
        client.close()
        sys.exit(3)
    # endif

    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
        print("Installed manifest " + manifest_path)
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif

    client.close()
# enddef


def kubectl_get_cluster_version(init_master_ip, user, key, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Finds current used cluster-version (for example as long as not all control-plane nodes are updated, the version should be the previous versio before the upgrade)

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param kubeconfig: str 
    :return: str
    """
    # example output
    # kubectl version
    #   Client Version: v1.28.13
    #   Kustomize Version: v5.0.4-0.20230601165947-6ce0bf390ce3
    #   Server Version: v1.28.13
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    kubernetes_cluster_version = None

    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " version")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)
    
    if stdout.channel.recv_exit_status() == 0:
        for line in cmd_output:
            if "Server Version:" in line:
                # older versions might use the long output and using "--short" with the kubectl command is later default and the argument is deprecated
                try:
                    unused_string, kubernetes_cluster_version = line.split(":")
                except ValueError:
                    unused_string, kubernetes_cluster_version_part = line.split("GitVersion:")
                    # we split again by comma and remove the quotes from the first element which is the cluster's version
                    kubernetes_cluster_version = kubernetes_cluster_version_part.split(",")[0].replace("\"","")
                # endtry
                kubernetes_cluster_version = kubernetes_cluster_version.strip()
                kubernetes_cluster_version = kubernetes_cluster_version.replace("v", "", 1)
            # endif
        # endfor
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
    
    client.close()
    return kubernetes_cluster_version
# enddef


def kubectl_drain_node(init_master_ip, user, key, node_name, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Drains a node

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param node_name: str
    :param kubeconfig: str
    :return: bool
    """
    # for draining a node we will use following command:
    # kubectl drain node_name --ignore-daemonsets --delete-emptydir-data --timeout 45s || kubectl drain node_name --ignore-daemonsets --delete-emptydir-data --disable-eviction
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    node_drained = False

    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " drain " + node_name + " --ignore-daemonsets --delete-emptydir-data --timeout 45s || kubectl --kubeconfig=" + kubeconfig + " drain " + node_name + " --ignore-daemonsets --delete-emptydir-data")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
        node_drained = True
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
    
    return node_drained
# endif


def kubectl_uncordon_node(init_master_ip, user, key, node_name, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Uncordon a node

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param node_name: str
    :param kubeconfig: str
    """
    # a simple kubectl uncordon node_name should be enough
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " uncordon " + node_name)
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# endif


def kubectl_get_cluster_name(init_master_ip, user, key, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Connects to init-master and gets the cluster's set name

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param kubeconfig: str
    :return: str
    """
    # kubectl config view --minify -o jsonpath='{.clusters[].name}'
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " config view --minify -o jsonpath='{.clusters[].name}'")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        return "\n".join(cmd_output)
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def kubectl_delete_node(init_master_ip, user, key, node_name, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Removes node from cluster

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param node_name: str
    :param kubeconfig: str
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " delete node " + node_name)
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def kubectl_get_nodes(init_master_ip, user, key, selector=None, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Gets all or nodes with a certain selector

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param selector: str
    :param kubeconfig: str
    :return: list
    """
    # kubectl get nodes --selector node-role.kubernetes.io/control-plane -o 'jsonpath={.items[*].metadata.name}'
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)
    
    if selector is not None:
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " get nodes --selector " + selector + " -o 'jsonpath={.items[*].metadata.name}'")
    else:
        stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " get nodes -o 'jsonpath={.items[*].metadata.name}'")
    # endif

    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)
    
    if stdout.channel.recv_exit_status() == 0 and len(cmd_output) == 1:
        # there only should be 1 line as output
        nodes = cmd_output[0].split()
        print("Node(s): " + ", ".join(nodes))

        client.close()
        return nodes
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def kubectl_get_nodes_status(init_master_ip, user, key, kubeconfig="/etc/kubernetes/admin.conf"):
    """
    Removes node from cluster

    :param init_master_ip: str
    :param user: str
    :param key: file
    :param kubeconfig: str
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command("kubectl --kubeconfig=" + kubeconfig + " get nodes -o wide")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef

