#!/usr/bin/env python

import ruamel.yaml
import paramiko
import time
import sys
import toml  # dnf install python3-toml on older python versions
import collections.abc
from jinja2 import Environment, FileSystemLoader
from packaging.version import Version


################################################################################
# default yaml config
yaml = ruamel.yaml.YAML()  # defaults to round-trip if no parameters given
yaml.preserve_quotes = True  # preserve quotes


def read_yaml_file(yaml_filepath):
    """
    Reads given yaml file and returns it as an array of dictionary if multiple documents are in one file or as a single dictonary.

    :type yaml_filepath: object
    """
    with open(yaml_filepath, 'r') as yfile:
        yaml_str = yfile.read()

    yaml_file = list(yaml.load_all(yaml_str))

    if len(yaml_file) == 1:
        return yaml_file[0]
    else:
        return yaml_file
    # endif
# enddef


def write_yaml_file(yaml_data, yaml_filepath):
    """
    Writes given yaml data to a set yaml file.

    :param yaml_data: list of dictionaries or single dictonary
    :param yaml_filepath: file
    """
    if isinstance(yaml_data, list):
        with open(yaml_filepath, 'w') as of:
            yaml.dump_all(yaml_data, stream=of)
    else:
        with open(yaml_filepath, 'w') as of:
            yaml.dump(yaml_data, stream=of)
# enddef


def load_template(template_path):
    """
    Reads given jinja2 template and returns it

    :param template_path: file
    :return: object 
    """
    env = Environment(loader=FileSystemLoader('./templates'), trim_blocks=True, lstrip_blocks=True)
    template = env.get_template(template_path)

    return template
# enddef


def render_file(file_path, template, values):
    """
    Renders given jinja2 template and writes it to a file

    :param file_path: file
    :param template: object
    :param values: dict
    """
    with open(file_path, "w") as file:
        file.write(template.render(values))
        file.close()
    # endwith
# enddef


def get_minor_version_string(version_string):
    """
    Reads given version string and extract only the major and minor version parts.

    :param version_string: str
    :return: str
    """
    # if we need the "major.minor" version string for something
    major_version = Version(version_string).major
    minor_version = Version(version_string).minor

    return str(major_version) + "." + str(minor_version)
# enddef


# blatantly stolen from https://stackoverflow.com/questions/4128144/replace-string-within-file-contents ;)
def inplace_file_change(filename, old_string, new_string):
    """
    Reads file and replaces set string and writes result to the same file

    :param filename: file
    :param old_string: str
    :param new_string: str
    """
    # Safely read the input filename using 'with'
    with open(filename) as f:
        s = f.read()
        if old_string not in s:
            print('"{old_string}" not found in {filename}.'.format(**locals()))
            sys.exit(1)

    # Safely write the changed content, if found in the file
    with open(filename, 'w') as f:
        print('Changing "{old_string}" to "{new_string}" in {filename}'.format(**locals()))
        s = s.replace(old_string, new_string)
        f.write(s)
# enddef


def upload_file(node_ip, user, key, source_file, destination_file):
    """
    Connects to node and uploads given file to node - file will be overwritten.

    :param node_ip: str
    :param user: str
    :param key: file
    :param source_file: file
    :param destination_file: file
    """
    # we need to upload the kube-vip.yaml and kubeadm config-files
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    sftp_upload_client = client.open_sftp()
    
    # TODO: make nicer - this is a little bit ugly - but it works for now
    previous_dir = ""
    # get all directory names from the upload file - we remove the first ("empty") and last element (which is the file)
    # for example "/etc/kubernetes/patches/kubeletconfiguration0.yaml"
    # will result in a list: ["etc", "kubernetes", "patches"]
    # then we check recursively if the needed folders exist and if needed we will have to create them
    # so that the upload doesn't fail
    dir_names = destination_file.split("/")
    del dir_names[0]  # remove first element that is usually ""
    del dir_names[-1]  # remove last element that is usually the file that will be uploaded
    for directory in dir_names:
        path_to_check = previous_dir + "/" + directory
        # print(path_to_check)
        try:
            sftp_upload_client.listdir(path_to_check)
        except FileNotFoundError:
            print("creating needed directory on node: " + path_to_check)
            # strange documentation https://docs.paramiko.org/en/latest/api/sftp.html#paramiko.sftp_client.SFTPClient.mkdir
            # with mode=0o755 the folder really gets created correctly
            sftp_upload_client.mkdir(path_to_check, mode=0o755)

        previous_dir = path_to_check
    # endfor

    print("Uploading file " + source_file + " to " + destination_file)
    sftp_upload_client.put(source_file, destination_file)

    client.close()
    sftp_upload_client.close()
# enddef


def download_file(node_ip, user, key, remote_source_file, local_destination_file):
    """
    Connects to node and uploads given file to node - file will be overwritten.

    :param node_ip: str
    :param user: str
    :param key: file
    :param remote_source_file: file
    :param local_destination_file: file
    """
    # we need to upload the kube-vip.yaml and kubeadm config-files
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    sftp_download_client = client.open_sftp()

    print("Downloading file " + remote_source_file + " to " + local_destination_file)
    sftp_download_client.get(remote_source_file, local_destination_file)

    client.close()
    sftp_download_client.close()
# enddef


def output_to_list(stdout, stderr):
    """
    Reads output from paramiko ssh-results and prettifies it to simple lists

    :param stdout: object
    :param stderr: object
    :return: list
    """
    cmd_output = []
    cmd_errors = []
    
    for line in iter(stdout.readline, ""):
        cmd_output.append(line.strip())
    for line in iter(stderr.readline, ""):
        cmd_errors.append(line.strip())
        
    return cmd_output, cmd_errors
# enddef


# TODO: currently not really used - change to curl
def download_file_from_internet(node_ip, user, key, download_file_url):
    """
    Downloads a file from given URL on node.
    
    :param node_ip: str
    :param user: str
    :param key: file
    :param download_file_url: str
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command("wget " + download_file_url)
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("Downloaded " + download_file_url)
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def install_packages(node_ip, user, key, packages: list, unattended=False, kubernetes_version=None):
    """
    Connects to node and installs given packages.

    :param node_ip: str
    :param user: str
    :param key: file
    :param packages: list
    :param unattended: bool
    :param kubernetes_version: str
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    
    # This should fix the problem that if a kubernetes_version is given, that there are no newer versions of cri-tools and/or kubernetes-cni from more recent repos are installed
    # Thanks to Aleks for the tipp using "--disablerepo '*kubernetes_*' --enablerepo '*kubernetes_<verision>*"
    if kubernetes_version is None:
        # "--disableexcludes=all" is normally needed, as the kube* packages are normally excluded for default to reduce the risk of accidentally updating them
        if dnf5_used(node_ip, user, key):
            stdin, stdout, stderr = client.exec_command("dnf install -y --setopt=disable_excludes=* " + " ".join(packages))
        else:
            stdin, stdout, stderr = client.exec_command("dnf install -y --disableexcludes=all " + " ".join(packages))
    else:
        kubernetes_minor_version = get_minor_version_string(kubernetes_version)
        kubernetes_version_reformatted = kubernetes_minor_version.replace(".", "_")
        if dnf5_used(node_ip, user, key):
            stdin, stdout, stderr = client.exec_command("dnf install -y --setopt=disable_excludes=* " + " ".join(packages) + " --disablerepo '*kubernetes_*' --enablerepo '*kubernetes_v" + kubernetes_version_reformatted + "*'")
        else:
            stdin, stdout, stderr = client.exec_command("dnf install -y --disableexcludes=all " + " ".join(packages) + " --disablerepo '*kubernetes_*' --enablerepo '*kubernetes_v" + kubernetes_version_reformatted + "*'")
    # endif

    dnf_output, dnf_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("\n".join(dnf_output))

    if len(dnf_errors) > 0:
        if stdout.channel.recv_exit_status() == 0:
            print("\nUSER-INTERVENTION MIGHT BE NEEDED, PLEASE CHECK!")
            print("\n".join(dnf_errors))
            if unattended:
                print("Packages seem to have been installed, yet we received something on STDERR - But unattended flag set, continuing...")
            else:
                user_input = input("Packages seem to have been installed, yet we received something on STDERR. Please, check for correctness? (yes/no): ")
                if user_input.lower() in ["yes", "y"]:
                    print("Continuing...")
                else:
                    print("Exiting - please, investigate on node...")
                    client.close()
                    sys.exit(2)
                # endif
            # endif
        else:
            print("\nDNF ERROR")
            print("\n".join(dnf_output))
            print("\n".join(dnf_errors))
            print("Please, check the error on the node!")
            client.close()
            sys.exit(2)
        # endif
    # endif

    client.close()
# enddef


def delete_cni_folder(node_ip, user, key):
    """
    Connects to give node and deletes the /etc/cni/net.d directory

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    
    stdin, stdout, stderr = client.exec_command("rm -rf /etc/cni/net.d")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("Deleted folder '/etc/cni/net.d'!")
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def delete_kubernetes_folder(node_ip, user, key):
    """
    Connects to give node and deletes the /etc/kubernetes directory

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command("rm -rf /etc/kubernetes")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("Deleted folder '/etc/kubernetes'!")
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def create_kubernetes_patches_folder(node_ip, user, key):
    """
    Connects to give node and creates the /etc/kubernetes/patches directory
    This folder is needed even if there are no patches defined for a node 
    since we added the "patches" config in the init/join-files!

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command("mkdir -p /etc/kubernetes/patches")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("Created folder '/etc/kubernetes/patches'!")
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def reboot_node(node_ip, user, key):
    """
    Connects to node and reboots it

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)

    stdin, stdout, stderr = client.exec_command("reboot")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        print("Reboot command sent")
    else:
        print("Something went wrong, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif

    client.close()
# enddef


# we must find the init-master
def get_init_master(cluster_values):
    """
    Reads the nodes-list and finds the node of type init-master

    :param cluster_values: object
    :return: list
    """
    for node in cluster_values["nodes"]:
        if "init-master" not in node["type"]:
            continue
        else:
            return node
        # endif
    # endfor
    
    print("No init-master found! Aborting...")
    sys.exit(1)
# enddef


def get_node_info(cluster_values, node_name):
    """
    Gets the node-info list from a selected node

    :param cluster_values: object
    :param node_name: str
    :return: list
    """
    for node in cluster_values["nodes"]:
        if node_name != node["nodeName"]:
            continue
        else:
            return node
        # endif
    # endfor

    print("No node-info for " + node_name + " found! Aborting...")
    sys.exit(1)
# enddef


def fix_containerd_config(node_ip, user, key, kubeadm_images: list, kube_image_repo, config_path):
    """
    Function for fixing some containerd parameters in its config.toml

    :param node_ip: str
    :param user: str
    :param key: file
    :param kubeadm_images: list
    :param kube_image_repo: str
    :param config_path: str
    """
    sandbox_image = None
    found_image = False
    # we need to get the "pause" image
    for image in kubeadm_images:
        if "pause" in image:
            sandbox_image = image.replace("registry.k8s.io", kube_image_repo)
            found_image = True
        else:
            continue
        # endif
    # endfor
    
    # we have to fix a few settings in the config.toml file like:
    # [plugins."io.containerd.grpc.v1.cri"]
    #   enable_unprivileged_icmp = true
    #   enable_unprivileged_ports = true
    #   sandbox_image = "registry.k8s.io/pause:3.9"
    #
    # [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
    #   SystemdCgroup = true
    #
    # for this we create a dictionary with the values we need
    needed_config_toml_settings = {
        "plugins": {
            "io.containerd.grpc.v1.cri": {
                "enable_unprivileged_icmp": True,
                "enable_unprivileged_ports": True,
                "sandbox_image": sandbox_image,
                "containerd": {
                    "runtimes": {
                        "runc": {
                            "options": {
                                "SystemdCgroup": True
                            }
                        }
                    }
                }
            }
        }
    }
    
    # blatantly stolen from https://www.learnbyexample.org/python-nested-dictionary/#deep-merge
    def deep_update(source, updates):
        for key, value in updates.items():
            if isinstance(value, collections.abc.Mapping):
                source[key] = deep_update(source.get(key, {}), value)
            else:
                source[key] = value
        return source
    # enddef

    if found_image:
        # python has a nice configparser modul that should be able to handle the TOML format - see https://docs.python.org/3/library/configparser.html
        download_file(node_ip, user, key, "/etc/containerd/config.toml", config_path + "/config.toml")
        
        with open(config_path + "/config.toml", "rb") as f:
            config_toml = toml.load(config_path + "/config.toml")
        
        # now we check for the needed keys and/or set them
        new_config_toml = deep_update(config_toml, needed_config_toml_settings)
        
        # we write the new file
        with open(config_path + "/config.toml", "w") as f:
            toml.dump(new_config_toml, f)
    
        upload_file(node_ip, user, key, config_path + "/config.toml", "/etc/containerd/config.toml")
    else:
        print("Sandbox image not found! Aborting...")
        sys.exit(1)
    # endif
# enddef


def dnf5_used(node_ip, user, key):
    """
    Connects to node and checks dnf version

    :param node_ip: str
    :param user: str
    :param key: file
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(node_ip, username=user, key_filename=key)
    version_found = False

    print("Checking dnf version...")
    stdin, stdout, stderr = client.exec_command("dnf --version | grep -P 'dnf.* version' | head -n1 | awk '{print $3}'")
    cmd_output, cmd_errors = stream_output(stdout)
    # cmd_output, cmd_errors = output_to_list(stdout, stderr)

    if stdout.channel.recv_exit_status() == 0:
        #print("Checking dnf version...")
        #print("\n".join(cmd_output))
        try:
            dnf_major_version = Version(cmd_output[0]).major
            print("dnf major version: " + str(dnf_major_version))
            version_found = True
        except IndexError as index_error:
            print("Could not determine dnf major version from output - probably version 4 - checking...")
        # endtry

        if not version_found:
            try:
                stdin, stdout, stderr = client.exec_command("dnf --version | head -n1")
                cmd_output, cmd_errors = stream_output(stdout)
                # cmd_output, cmd_errors = output_to_list(stdout, stderr)
                dnf_major_version = Version(cmd_output[0]).major
                print("dnf major version: " + str(dnf_major_version))
            except:
                print("Could not determine dnf major version from output. Aborting...")
                sys.exit(1)
            # endtry
        # endif
            
        client.close()
        if dnf_major_version >= 5:
            return True
        else:
            return False
        # endif
    else:
        print("Something went wrong while trying to check the dnf version, please check!")
        print("\n".join(cmd_output))
        print("\n".join(cmd_errors))
        client.close()
        sys.exit(2)
    # endif
# enddef


def stream_output(channel):
    """
    Reading and printing output nearly live to console

    :param channel: object
    :return: cmd_output, cmd_errors
    """
    cmd_output_string = ""
    cmd_errors_string = ""

    stream_output_channel = channel.channel

    while not stream_output_channel.exit_status_ready():
        if stream_output_channel.recv_ready():
            output = stream_output_channel.recv(1024).decode('utf-8')
            cmd_output_string = cmd_output_string + output
            print(output, end='')

        if stream_output_channel.recv_stderr_ready():
            err = stream_output_channel.recv_stderr(1024).decode('utf-8')
            cmd_errors_string = cmd_errors_string + err
            print(err, end='')

        time.sleep(0.1)

    # read remaining data after exit
    while stream_output_channel.recv_ready():
        output = stream_output_channel.recv(1024).decode('utf-8')
        cmd_output_string = cmd_output_string + output
        print(output, end='')

    while stream_output_channel.recv_stderr_ready():
        err = stream_output_channel.recv_stderr(1024).decode('utf-8')
        cmd_errors_string = cmd_errors_string + err
        print(err, end='')

    # we create lists from the output - we need lists later for some checks
    cmd_output = cmd_output_string.split('\n')
    cmd_errors = cmd_errors_string.split('\n')

    # we remove empty strings from the output
    cmd_output = list(filter(None, map(str.strip, cmd_output)))
    cmd_errors = list(filter(None, map(str.strip, cmd_errors)))

    return cmd_output, cmd_errors
