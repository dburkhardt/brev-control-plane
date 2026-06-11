from brev_control_plane.checks import build_check_command, parse_check_output


def test_build_check_command_collects_generic_machine_capabilities():
    command = build_check_command()

    assert command.startswith("bash -lc ")
    assert "ifconfig.me" in command
    assert "docker --version" in command
    assert "sudo docker --version" in command
    assert "python3 --version" in command
    assert "df -h /" in command


def test_parse_check_output_normalizes_machine_report():
    output = "\n".join(
        [
            "INSTANCE=host-a",
            "EGRESS_IP=203.0.113.10",
            "UNAME=Linux host-a 6.1",
            "USER=ubuntu",
            "DOCKER_PATH=/usr/bin/docker",
            "DOCKER_DIRECT=Docker version 25.0.0, build abc",
            "DOCKER_SUDO=Docker version 25.0.0, build abc",
            "PYTHON3=Python 3.11.8",
            "DISK_ROOT=/dev/root 30G 10G 20G 34% /",
        ]
    )

    assert parse_check_output(output) == {
        "instance": "host-a",
        "egress_ip": "203.0.113.10",
        "uname": "Linux host-a 6.1",
        "user": "ubuntu",
        "docker_path": "/usr/bin/docker",
        "docker_access": "direct",
        "docker_version": "Docker version 25.0.0, build abc",
        "python3": "Python 3.11.8",
        "disk_root": "/dev/root 30G 10G 20G 34% /",
    }


def test_parse_check_output_uses_sudo_docker_when_direct_access_fails():
    output = "\n".join(
        [
            "DOCKER_DIRECT=permission denied",
            "DOCKER_SUDO=Docker version 24.0.0, build def",
        ]
    )

    report = parse_check_output(output)

    assert report["docker_access"] == "sudo"
    assert report["docker_version"] == "Docker version 24.0.0, build def"


def test_parse_check_output_marks_docker_missing_without_version():
    report = parse_check_output("DOCKER_DIRECT=\nDOCKER_SUDO=not found\n")

    assert report["docker_access"] == "missing"
    assert report["docker_version"] == ""
