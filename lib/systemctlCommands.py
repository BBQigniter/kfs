#!/usr/bin/env python

import paramiko
import sys
from time import sleep
from lib.kfsTools import stream_output


def systemctl_restart_kubelet(node_ip, user, key):
    """
    Connect to node and restart kubelet via systemctl

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    print("Restarting kubelet!")
    stdin, stdout, stderr = client.exec_command('systemctl restart kubelet')
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("Restarted kubelet waiting for about 10 seconds as graceperiod so that pods are able to recover...")
        sleep(10)
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif

    client.close()
# enddef


def systemctl_autostart_containerd_kubelet(node_ip, user, key, enable=True):
    """
    Connect to node and enable kubelet and containerd via systemctl

    :param node_ip: str
    :param user: str
    :param key: file
    :param enable: bool
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    if enable:
        print("Starting containerd, kubelet and enabling it to autostart!")
        stdin, stdout, stderr = client.exec_command('systemctl enable containerd kubelet --now')
    else:
        print("Stopping containerd, kubelet and disabling it to autostart!")
        stdin, stdout, stderr = client.exec_command('systemctl disable containerd kubelet --now')

    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
        
        # still we output for warnings
        if len(cmd_errors) > 0:
            print("There seem to be some warnings, please check!")
            print("\n".join(cmd_errors))
        # endif
        
        if enable:
            print("Started containerd and kubelet.")
        else:
            systemctl_kill_all_containers(node_ip, user, key)
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif

    client.close()
# enddef


def systemctl_daemon_reload(node_ip, user, key):
    """
    Connect to node and reload systemd

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    print("Reloading systemd!")
    stdin, stdout, stderr = client.exec_command('systemctl daemon-reload')
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("Reloaded systemd")
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif

    client.close()
# enddef


# can be executed if containerd gets stopped - else some containers might still be running
# see https://github.com/containerd/containerd/issues/7076 
def systemctl_kill_all_containers(node_ip, user, key):
    """
    Connect to node and reload systemd

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    print("Stopping all containers that might be left!")
    stdin, stdout, stderr = client.exec_command('systemctl stop cri-containerd*')

    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(cmd_output))
        
        if len(cmd_errors) > 0:
            print("There seem to be some warnings, please check!")
            print("\n".join(cmd_errors))
        # endif
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif

    client.close()
# enddef
