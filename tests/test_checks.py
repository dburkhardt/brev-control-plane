from brev_control_plane.checks import build_check_command, parse_check_output


def test_build_check_command_collects_generic_machine_capabilities():
    command = build_check_command()

    assert command.startswith("bash -lc ")
    assert "ifconfig.me" in command
    assert "timeout 10 docker --version" in command
    assert "docker ps >/dev/null" in command
    assert "timeout 10 sudo -n docker --version" in command
    assert "sg docker -c" in command
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
            "DOCKER_DIRECT_VERSION=Docker version 25.0.0, build abc",
            "DOCKER_DIRECT_API=ok",
            "DOCKER_SUDO_VERSION=Docker version 25.0.0, build abc",
            "DOCKER_SUDO_API=ok",
            "DOCKER_SG_VERSION=Docker version 25.0.0, build abc",
            "DOCKER_SG_API=ok",
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
            "DOCKER_DIRECT_VERSION=Docker version 24.0.0, build def",
            "DOCKER_DIRECT_API=failed",
            "DOCKER_SUDO_VERSION=Docker version 24.0.0, build def",
            "DOCKER_SUDO_API=ok",
        ]
    )

    report = parse_check_output(output)

    assert report["docker_access"] == "sudo"
    assert report["docker_version"] == "Docker version 24.0.0, build def"


def test_parse_check_output_uses_sg_when_session_lacks_docker_group():
    output = "\n".join(
        [
            "DOCKER_DIRECT_VERSION=Docker version 29.5.3, build d1c06ef",
            "DOCKER_DIRECT_API=failed",
            "DOCKER_SUDO_VERSION=sudo: a password is required",
            "DOCKER_SUDO_API=failed",
            "DOCKER_SG_VERSION=Docker version 29.5.3, build d1c06ef",
            "DOCKER_SG_API=ok",
        ]
    )

    report = parse_check_output(output)

    assert report["docker_access"] == "sg"
    assert report["docker_version"] == "Docker version 29.5.3, build d1c06ef"


def test_parse_check_output_prefers_sg_over_sudo_when_both_work():
    output = "\n".join(
        [
            "DOCKER_DIRECT_VERSION=Docker version 29.5.3, build d1c06ef",
            "DOCKER_DIRECT_API=failed",
            "DOCKER_SUDO_VERSION=Docker version 29.5.3, build d1c06ef",
            "DOCKER_SUDO_API=ok",
            "DOCKER_SG_VERSION=Docker version 29.5.3, build d1c06ef",
            "DOCKER_SG_API=ok",
        ]
    )

    report = parse_check_output(output)

    assert report["docker_access"] == "sg"
    assert report["docker_version"] == "Docker version 29.5.3, build d1c06ef"


def test_parse_check_output_marks_docker_missing_without_version():
    report = parse_check_output(
        "DOCKER_DIRECT_VERSION=\nDOCKER_DIRECT_API=failed\n"
        "DOCKER_SUDO_VERSION=not found\nDOCKER_SUDO_API=failed\n"
        "DOCKER_SG_VERSION=not found\nDOCKER_SG_API=failed\n"
    )

    assert report["docker_access"] == "missing"
    assert report["docker_version"] == ""
