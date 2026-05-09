#!/usr/bin/env python

import paramiko
import sys
import re
from lib.systemctlCommands import systemctl_autostart_containerd_kubelet
from lib.kfsTools import delete_cni_folder, delete_kubernetes_folder, reboot_node, stream_output


def kubeadm_get_kube_token_and_hash(init_master_ip, user, key):
    """
    Connect to init-master and create a token and cert-hash. The values will be extracted via regex.

    :param init_master_ip: str
    :param user: str
    :param key: file
    :return: tuple
    """
    # connect to init-master and execute `kubeadm token create --print-join-command`
    # extract needed token and cert-hahs from output
    # example output: kubeadm join 10.12.100.175:6443 --token w0lx36.6xw0t3d599rujvr3 --discovery-token-ca-cert-hash sha256:96ef32aa6c7d39dafdf8c2ec063a1b652988b23a47ba2e0964d25a6c08b350b0
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    token = None
    cert_hash = None
    # regex search patterns
    token_regex = r'(?<=--token\s).{6}\..{16}'
    cert_hash_regex = r'(?<=--discovery-token-ca-cert-hash\s)sha256\:.*'

    stdin, stdout, stderr = client.exec_command("kubeadm token create --print-join-command")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        for line in cmd_output:
            token = re.findall(token_regex, line)
            cert_hash = re.findall(cert_hash_regex, line)

        client.close()
        return (token[0], cert_hash[0])
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif

    # for debugging
    # client.close()
    #
    # return ("w0lx36.6xw0t3d599rujvr3", "sha256:96ef32aa6c7d39dafdf8c2ec063a1b652988b23a47ba2e0964d25a6c08b350b0")
# enddef


def kubeadm_get_kube_cert_key(init_master_ip, user, key):
    """
    Connect to init-master and create a cert-key.
    
    :param init_master_ip: str
    :param user: str
    :param key: file
    :return: string
    """
    # connect to init-master and execute `kubeadm init phase upload-certs --upload-certs`
    # extract needed cert-key from output
    # example output:
    # I1009 15:22:59.432027  839049 version.go:256] remote version is much newer: v1.31.0; falling back to: stable-1.29
    # [upload-certs] Storing the certificates in Secret "kubeadm-certs" in the "kube-system" Namespace
    # [upload-certs] Using certificate key:
    # 4c6e6a3b1432d1223fdf649d727ce8f95a329cb392ca8273179e684af4c8377a

    # blatantly stolen from https://stackoverflow.com/questions/2170900/get-first-list-index-containing-sub-string ;)
    def index_containing_substring(the_list, substring):
        for i, s in enumerate(the_list):
            if substring in s:
                return i
        return -1

    # enddef

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(init_master_ip, username=user, key_filename=key)

    search_string = "Using certificate key:"
    cert_key = None

    stdin, stdout, stderr = client.exec_command("kubeadm init phase upload-certs --upload-certs")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    # the output mostly will create some warnings to stderr if the node cannot reach the internet - we ignore those
    # example:
    # W1014 14:05:39.767026 2652621 version.go:104 could not fetch a Kubernetes version from the internet: unable to get URL ...
    # W1014 14:05:39.767109 2652621 version.go:105 falling back to the local client version: v1.28.13
    if stdout.channel.recv_exit_status() == 0:
        get_index_before_key = index_containing_substring(cmd_output, search_string)

        if get_index_before_key != -1:
            cert_key = cmd_output[get_index_before_key + 1]
        else:
            print("Something went wrong, please check!")
            print("\n".join(cmd_output))
            print("\n".join(cmd_errors))
            client.close()
            sys.exit(2)
        # endif

        client.close()
        return cert_key
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif

    # for debugging
    # client.close()
    #
    # return "4c6e6a3b1432d1223fdf649d727ce8f95a329cb392ca8273179e684af4c8377a"
# enddef


def kubadm_join_cluster(node_ip, user, key, join_config_file):
    """
    Connect to node and execute simple kubeadm join command.

    :param node_ip: str
    :param user: str
    :param key: file
    :param join_config_file: file
    """
    # will execute following command on main-node
    # kubeadm init --upload-certs --config="/root/init-master-config.yaml"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    systemctl_autostart_containerd_kubelet(node_ip, user, key)
    stdin, stdout, stderr = client.exec_command('kubeadm join --config="' + join_config_file + '"')
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))

        client.close()
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def kubeadm_upgrade_plan(node_ip, user, key, unattended=False):
    """
    Executes the needed "kubeadm upgrade plan" on the selected node

    :param node_ip: str
    :param user: str
    :param key: file
    :param unattended: bool  # currently ignored - see comments further below
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command('kubeadm upgrade plan')
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
        
        # For safety reason we simply ignore the unattanded flag here because currently there is no nice way to handle it,
        # if there would really be a manual change needed. I currently never have seen one yet, that there was a manual change needed.
        # I let this here for maybe future changes - I first would need some proper message to know how to best handle this.
        #
        # Anyway the `kubeadm upgrade plan`-command is only executed once - so the unattended flag will take effect for the rest of
        # the upgrade workflow.
        """
        if unattended:
            print("Unattended flag set, we assume no manual changes are needed, continuing...")
        else:
            user_input = input("If manual changes are needed, please do them now as described, else answer with 'yes' to continue. Is the upgrade plan looking OK? (yes/no): ")
            if user_input.lower() in ["yes", "y"]:
                print("Continuing...")
            else:
                print("Exiting - please, investigate on node...")
                client.close()
                sys.exit(2)
            # endif
        # endif
        """
        
        user_input = input("If manual changes are needed, please do them now as described, else answer with 'yes' to continue. Is the upgrade plan looking OK? (yes/no): ")
        if user_input.lower() in ["yes", "y"]:
            print("Continuing...")
        else:
            print("Exiting - please, investigate on node...")
            client.close()
            sys.exit(2)
        # endif

        client.close()
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def kubeadm_upgrade_apply_version(node_ip, user, key, kube_version, has_patch=False):
    """
    Executes "kubeadm upgrade apply v<version> --yes" on the selected node

    :param node_ip: str
    :param user: str
    :param key: file
    :param kube_version: str
    :param has_patch: bool
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    
    if has_patch:
        # the patches-path must unfortunately exist, else this fails
        stdin, stdout, stderr = client.exec_command('kubeadm upgrade apply v' + kube_version + ' --yes --patches /etc/kubernetes/patches')
    else:
        stdin, stdout, stderr = client.exec_command('kubeadm upgrade apply v' + kube_version + ' --yes')
    # endif

    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))

        client.close()
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def kubeadm_upgrade_node(node_ip, user, key, has_patch=False):
    """
    Executes "kubeadm upgrade node" on the selected node

    :param node_ip: str
    :param user: str
    :param key: file
    :param has_patch: bool
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    
    if has_patch:
        stdin, stdout, stderr = client.exec_command('kubeadm upgrade node --patches /etc/kubernetes/patches')
    else:
        stdin, stdout, stderr = client.exec_command('kubeadm upgrade node')
    # endif

    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))

        client.close()
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def kubeadm_reset_node(node_ip, user, key):
    """
    Executes "kubeadm reset --force" on the selected node

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command('kubeadm reset --force')
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
        
        # after successful reset we stop kubelet and containerd
        systemctl_autostart_containerd_kubelet(node_ip, user, key, enable=False)

        # then we have to delete the folder /etc/cni/net.d
        delete_cni_folder(node_ip, user, key)

        # we also delete folder /etc/kubernetes
        delete_kubernetes_folder(node_ip, user, key)

        # and finally we have to reboot the node
        reboot_node(node_ip, user, key)

        client.close()
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def kubeadm_get_images_list(node_ip, user, key):
    # must check if the "sandbox_image"-repository path is set to kube.image.repository's value
    # the correct sandbox_image can be found for example via:
    # kubeadm config images list --config init-master-config.yaml
    """
    Executes "kubeadm reset --force" on the selected node

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    
    # we need this to fix the containerd config.toml - this command will output the list with default registry.k8s.io - we will need to replace it accordingly!
    stdin, stdout, stderr = client.exec_command('kubeadm config images list')
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        client.close()
        return cmd_output
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
# enddef


# TODO: several parts of the addNode and createCluster are pretty similar, we might add this into a better function to be easier to reuse
def kubeadm_prepare_and_join(init_master_ip, init_master_user, init_master_key, cluster_values, node, with_checks=False):
    # we render the kubernetes-repo-file in case we want to use it in future
    # render_file(config_path + "/kubernetes-" + minor_version_string + ".repo", dnf_repo, cluster_values)
    pass
# enddef


