from __future__ import annotations

import itertools
import os
import shlex
import signal
import textwrap
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Generator, Generic, NoReturn, Self, TypeVar

import colorama as c

from .._private.logging import MultihostLogger

if TYPE_CHECKING:
    from .. import MultihostHost

__all__ = [
    "Bash",
    "Connection",
    "ConnectionError",
    "Powershell",
    "Process",
    "ProcessError",
    "ProcessErrorType",
    "ProcessInputBuffer",
    "ProcessInputBufferType",
    "ProcessLogLevel",
    "ProcessResult",
    "ProcessResultType",
    "ProcessType",
    "Shell",
]


class ProcessLogLevel(Enum):
    """
    Process log level.
    """

    Silent = auto()
    """
    No log messages are produced.
    """

    Short = auto()
    """
    Command execution and return code is logged. Its output is omitted.
    """

    Full = auto()
    """
    Command execution, its return code and output is logged.
    """

    Error = auto()
    """
    Only log the command and its result on non-zero exit code.
    """


class ProcessInputBuffer(ABC):
    """
    Process' input buffer.

    Allows to write into stdin of the process.
    """

    @abstractmethod
    def write(self, data: str | bytes) -> None:
        """
        Write data to stdin.

        :param data: Data to write.
        :type data: str | bytes
        """
        pass


class ProcessError(Exception):
    """
    Process' error.
    """

    def __init__(
        self,
        id: int,
        command: str,
        rc: int,
        cwd: str | None,
        env: dict[str, Any],
        input: str | bytes | None,
        stdout: list[str],
        stderr: list[str],
    ) -> None:
        pretty_env = ""
        for key, value in env.items():
            pretty_env += f"{key}={value}\n"

        str_stdout: str = "\n".join(stdout)
        str_stderr: str = "\n".join(stderr)

        def dumps(value) -> str:
            if not value:
                return ""

            return "\n" + textwrap.indent(value, " " * 20)

        super().__init__(
            textwrap.dedent(
                f"""
                Command #{id} exited with return code {rc}:
                  Command:{dumps(command)}
                  CWD:{dumps(cwd)}
                  Env:{dumps(pretty_env.strip())}
                  Output:{dumps(str_stdout)}
                  Error output:{dumps(str_stderr)}
                """
            )
        )

        self.id: int = id
        """Command autogenerated ID."""

        self.command: str = command
        """Failed command."""

        self.rc: int = rc
        """Return code."""

        self.cwd: str | None = cwd
        """Working directory."""

        self.env: dict[str, Any] = env
        """Additional environment variables."""

        self.input: str | bytes | None = input
        """Input data."""

        self.stdout: str = str_stdout
        """Standard output."""

        self.stderr: str = str_stderr
        """Standard error output."""

        self.stdout_lines: list[str] = stdout
        """Standard output as list of lines."""

        self.stderr_lines: list[str] = stderr
        """Standard error output as list of lines."""


ProcessErrorType = TypeVar("ProcessErrorType", bound=ProcessError)
"""Generic process error type. Must be a subclass of :class:`ProcessError`."""


class ProcessResult(Generic[ProcessErrorType]):
    """
    Process' result.
    """

    def __init__(self, rc: int, stdout: list[str], stderr: list[str], error: ProcessErrorType) -> None:
        """
        :param rc: Return code.
        :type rc: int
        :param stdout: Standard output, line by line.
        :type stdout: list[str]
        :param stderr: Standard error output, line by line.
        :type stderr: list[str]
        :param error: Process error object that can be raised manually with :meth:`throw`.
        :type error: ProcessErrorType
        """
        self.rc: int = rc
        """Return code."""

        self.stdout: str = "\n".join(stdout)
        """Standard output."""

        self.stderr: str = "\n".join(stderr)
        """Standard error output."""

        self.stdout_lines: list[str] = stdout
        """Standard output, line by line."""

        self.stderr_lines: list[str] = stderr
        """Standard error output, line by line."""

        self.error: ProcessErrorType = error
        """Process error object that can be raised manually with :meth:`throw`."""

    def throw(self) -> NoReturn:
        """
        Raise ProcessErrorType for this command manually.

        The error is available to raise even if return code is 0. Therefore the
        caller may choose to raise the error on any condition, not just the
        return code.

        :raises ValueError: If no error is set.
        :raises ProcessErrorType: Error generated for this process result.
        """
        if self.error is None:
            raise ValueError("No error is set.")

        raise self.error


ProcessResultType = TypeVar("ProcessResultType", bound=ProcessResult)
"""Generic process result type. Must be a subclass of :class:`ProcessResult`."""


ProcessInputBufferType = TypeVar("ProcessInputBufferType", bound=ProcessInputBuffer)
"""Generic process input buffer type. Must be a subclass of :class:`ProcessInputBuffer`."""


class Process(ABC, Generic[ProcessResultType, ProcessInputBufferType]):
    """
    Process manager.
    """

    _genid = itertools.count()

    def __init__(
        self,
        *,
        command: str,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
        input: str | bytes | None = None,
        shell: Shell,
        logger: MultihostLogger,
        log_level: ProcessLogLevel,
        blocking_call: bool,
        additional_log_data: dict[str, Any] | None = None,
    ) -> None:
        """
        :param command: Command to execute.
        :type command: str
        :param cwd: Working directory, defaults to None
        :type cwd: str | None, optional
        :param env: Additional environment variables, defaults to None
        :type env: dict[str, Any] | None, optional
        :param input: Content of standard input, defaults to None
        :type input: str | bytes | None, optional
        :param shell: Shell that will execute the command
        :type shell: Shell
        :param logger: Multihost logger.
        :type logger: MultihostLogger
        :param log_level: Log level.
        :type log_level: ProcessLogLevel
        :param blocking_call: Is this a blocking execution?
        :type blocking_call: bool
        :param additional_log_data: Additional data that will be added to the
            log messages, defaults to None
        :type additional_log_data: dict[str, Any] | None, optional
        """
        self.id: int = next(self._genid) + 1
        """Autogenerated command ID."""

        self.command: str = textwrap.dedent(command).strip()
        """Executed command."""

        self.cwd: str | None = cwd
        """Working directory."""

        self.env: dict[str, Any] = env if env is not None else {}
        """Additional environment variables."""

        self.input: str | bytes | None = input
        """Input data."""

        self.shell: Shell = shell
        """Shell that executes the command."""

        self.logger: MultihostLogger = logger
        """Multihost logger."""

        self.log_level: ProcessLogLevel = log_level
        """Log level."""

        self.blocking_call: bool = blocking_call
        """True if this is a blocking call."""

        self.additional_log_data: dict[str, Any] = additional_log_data if additional_log_data is not None else {}
        """Additional data that will be added to the log messages."""

        self.full_command_line: str = self.shell.build_command_line(self.command, cwd=self.cwd, env=self.env)
        """Full command line that will be executed."""

        # Overwrite log level if requested.
        debug = os.getenv("MH_CONNECTION_DEBUG", "no")
        if debug.lower() in ["true", "yes", "1"]:
            self.log_level = ProcessLogLevel.Full

    @property
    @abstractmethod
    def in_progress(self) -> bool:
        """
        :return: True if the process is already started and running.
        :rtype: bool
        """
        pass

    @property
    @abstractmethod
    def stdout(self) -> Generator[str, None, None]:
        """
        Standard output, returns generator which yields output line by line.

        .. code-block:: python

            # Read single line, this will block until there is a line to read or
            # EOF is reached
            line = next(process.stdout)

            # Read all lines, this will block until EOF or EOF is reached
            lines = list(process.stdout)

            # Iterate over all lines
            for line in process.stdout:
                pass

        :raises RuntimeError: If the process is not running.
        :return: Standard output generator.
        :rtype: Generator[str, None, None]
        """
        pass

    @property
    @abstractmethod
    def stderr(self) -> Generator[str, None, None]:
        """
        Standard error output, returns generator which yields error output line
        by line.

        .. code-block:: python

            # Read single line, this will block until there is a line to read or
            # EOF is reached
            line = next(process.stderr)

            # Read all lines, this will block until EOF or EOF is reached
            lines = list(process.stderr)

            # Iterate over all lines
            for line in process.stderr:
                pass

        :raises RuntimeError: If the process is not running.
        :return: Standard error output generator.
        :rtype: Generator[str, None, None]
        """
        pass

    @property
    @abstractmethod
    def stdin(self) -> ProcessInputBufferType:
        """
        Command's standard input.

        Call :meth:`send_eof` to close the standard input and notify the process
        that there will be no more input data.

        .. code-block:: python

            # Write data
            process.stdin.write('Hello World')

            # Send EOF to indicate that there will be no more input data.
            process.send_eof()

        :raises RuntimeError: If the process is not running.
        :return: Standard input file.
        :rtype: ProcessInputBufferType
        """
        pass

    def run(self) -> Self:
        """
        Execute the command.

        :return: Self.
        :rtype: Self
        """
        if self.log_level in (ProcessLogLevel.Short, ProcessLogLevel.Full):
            self.logger.info(
                self.__msg_execution(),
                extra={
                    "data": {
                        **self.additional_log_data,
                        "Shell": self.shell.shell_command,
                        "Command": self.command,
                        "Input": self.input,
                        "Working directory": self.cwd,
                        "Extra environment": self.env,
                    }
                },
            )

        self._run()

        return self

    def wait(self, raise_on_error: bool = True) -> ProcessResultType:
        """
        Wait for the command to finish.

        EOF is send to standard input to indicate that there will be no
        additional input data. Then it waits for the command to finish.

        :param raise_on_error: If True, :class:`ProcessError` is raised on
            non-zero return code, defaults to True
        :type raise_on_error: bool, optional
        :raises ProcessError: If ``raise_on_error`` is True and the command
            exited with non-zero return code.
        :return: Command result.
        :rtype: ProcessResultType
        """
        if not self.in_progress:
            raise RuntimeError("Calling wait on process that has not yet started.")

        result = self._wait()

        if self.log_level == ProcessLogLevel.Error and result.rc != 0:
            self.logger.error(
                self.__msg_completed_async(result.rc),
                extra={
                    "data": {
                        **self.additional_log_data,
                        "Shell": self.shell.shell_command,
                        "Command": self.command,
                        "Input": self.input,
                        "Working directory": self.cwd,
                        "Extra environment": self.env,
                        "Output": result.stdout,
                        "Error output": result.stderr,
                    }
                },
            )

        if self.blocking_call:
            match self.log_level:
                case ProcessLogLevel.Short:
                    self.logger.info(self.__msg_completed_sync(result.rc))
                case ProcessLogLevel.Full:
                    self.logger.info(
                        self.__msg_completed_sync(result.rc),
                        extra={
                            "data": {
                                "Output": result.stdout,
                                "Error output": result.stderr,
                            }
                        },
                    )
                case _:
                    pass
        else:
            match self.log_level:
                case ProcessLogLevel.Short:
                    self.logger.info(
                        self.__msg_completed_async(result.rc),
                        extra={
                            "data": {
                                **self.additional_log_data,
                                "Shell": self.shell.shell_command,
                                "Command": self.command,
                                "Input": self.input,
                                "Working directory": self.cwd,
                                "Extra environment": self.env,
                            }
                        },
                    )
                case ProcessLogLevel.Full:
                    self.logger.info(
                        self.__msg_completed_async(result.rc),
                        extra={
                            "data": {
                                **self.additional_log_data,
                                "Shell": self.shell.shell_command,
                                "Command": self.command,
                                "Input": self.input,
                                "Working directory": self.cwd,
                                "Extra environment": self.env,
                                "Output": result.stdout,
                                "Error output": result.stderr,
                            }
                        },
                    )
                case _:
                    pass

        if raise_on_error and result.rc != 0:
            result.throw()

        return result

    @abstractmethod
    def _run(self) -> None:
        """
        Execute the command.

        This is an internal method called by :meth:`run` after executing
        generic code.
        """
        pass

    @abstractmethod
    def _wait(self) -> ProcessResultType:
        """
        Wait for the command to finish.

        EOF is send to standard input to indicate that there will be no
        additional input data. Then it waits for the command to finish.

        This is an internal method called by :meth:`run` after executing
        generic code.

        :return: Command result.
        :rtype: ProcessResultType
        """
        pass

    @abstractmethod
    def send_eof(self) -> None:
        """
        Send EOF to standard input to indicate that there will be no more
        input data.

        :raises RuntimeError: If the process is not running.
        """
        pass

    @abstractmethod
    def send_signal(self, sig: signal.Signals) -> None:
        """
        Send signal to the running process.

        :param sig: A signal constant from ``signal``, e.g. ``signal.SIGUSR1``.
        :type sig: signal.Signals
        :raises RuntimeError: If the process is not running.
        """
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info):
        self.wait()

    def __msg_id(self) -> str:
        return self.logger.colorize(f"#{self.id}", c.Style.BRIGHT, c.Fore.BLUE)

    def __msg_rc(self, rc: int) -> str:
        if rc == 0:
            return self.logger.colorize(rc, c.Style.BRIGHT, c.Fore.GREEN)

        return self.logger.colorize(rc, c.Style.BRIGHT, c.Fore.RED)

    def __msg_execution(self) -> str:
        return f'{self.logger.colorize("Executing command", c.Style.BRIGHT)} ' + self.__msg_id()

    def __msg_completed_sync(self, rc: int) -> str:
        return "Previous command completed with exit code " + self.__msg_rc(rc)

    def __msg_completed_async(self, rc: int) -> str:
        return (
            self.logger.colorize("Command ", c.Style.BRIGHT)
            + self.__msg_id()
            + self.logger.colorize(" completed with exit code ", c.Style.BRIGHT)
            + self.__msg_rc(rc)
        )


ProcessType = TypeVar("ProcessType", bound=Process)
"""Generic process type. Must be a subclass of :class:`Process`."""


class ConnectionError(Exception):
    """
    Unable to connect to the host.
    """

    pass


class Connection(ABC, Generic[ProcessType, ProcessResultType]):
    """
    Abstract connection to the host.

    This is an abstract class. The following examples utilizes
    :class:`ssh.SSHClient` which implements this interface.

    .. code-block:: python
        :caption: Example: Blocking call

        # Connect to SSH server, it is automatically disconnected when leaving
        the with statement with SSHClient(host, user=username,
        password=password, logger=logger) as ssh:
            result = ssh.run('echo Hello World') print(result.rc)
            print(result.stdout)

            result = ssh.run('cat', input='Hello World') print(result.rc)
            print(result.stdout)

    .. code-block:: python
        :caption: Example: Non-blocking call

        # Connect to SSH server, it is automatically disconnected when leaving
        the with statement with SSHClient(host, user=username,
        password=password, logger=logger) as ssh:
            # The process is executed, but it does not block. In order to wait
            for it to finish, run process.wait() process = ssh.async_run('echo
            Hello World') result = process.wait() print(result.rc)
            print(result.stdout)

            # You can write to stdin directly in asynchronous run process =
            ssh.async_run('cat') process.stdin.write('Hello World')
            process.send_eof() result = process.wait() print(result.rc)
            print(result.stdout)

            # You can also work with inputs and outputs more interactively. #
            The process is automatically waited when leaving the with statement.
            with ssh.async_run('bash') as process:
                process.stdin.write('echo Hello World\\n')
                print(next(process.stdout))

                process.stdin.write('echo This works as well\\n')
                print(next(process.stdout))

    .. note::

        It is possible to set ``MH_CONNECTION_DEBUG=yes`` environment variable
        to log output and exit status to from commands, regardless of what log
        level is used. This essentially enforces the
        :attr:`ProcessLogLevel.Full` level.
    """

    def __init__(
        self,
        *,
        shell: Shell,
        logger: MultihostLogger,
    ) -> None:
        """
        :param shell: Shell used to run commands and scripts.
        :type shell: str, optional
        :param logger: Multihost logger.
        :type logger: MultihostLogger
        """
        self.shell: Shell = shell
        """Shell used to run commands and scripts."""

        self.logger: MultihostLogger = logger
        """Multihost logger."""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """
        :return: True if the connection is established, False otherwise.
        :rtype: bool
        """
        pass

    @abstractmethod
    def connect(self) -> None:
        """
        Connect to the host.

        :raises ConnectionError: If connection can not be established.
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """
        Disconnect.
        """
        pass

    @abstractmethod
    def create_process(
        self,
        *,
        command: str,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
        input: str | bytes | None = None,
        log_level: ProcessLogLevel,
        blocking_call: bool,
    ) -> ProcessType:
        """
        Create a new process.

        :param command: Command to execute.
        :type command: str
        :param cwd: Working directory, defaults to None
        :type cwd: str | None, optional
        :param env: Additional environment variables, defaults to None
        :type env: dict[str, Any] | None, optional
        :param input: Content of standard input, defaults to None
        :type input: str | bytes | None, optional
        :param log_level: Log level.
        :type log_level: ProcessLogLevel
        :param blocking_call: Is this a blocking execution?
        :type blocking_call: bool
        :return: Newly created process that is not yet running.
        :rtype: ProcessType
        """
        pass

    def async_run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
        input: str | bytes | None = None,
        log_level: ProcessLogLevel = ProcessLogLevel.Full,
    ) -> ProcessType:
        """
        Non-blocking command call.

        The command is run under shell specified in the constructor and it is
        executed immediately, however it does not wait for the command to
        finish.

        :param command: Command to run.
        :type command: str
        :param cwd: Working directory, defaults to None (= do not change)
        :type cwd: str | None, optional
        :param env: Additional environment variables, defaults to None
        :type env: dict[str, Any] | None, optional
        :param input: Content of standard input, defaults to None
        :type input: str | bytes | None, optional
        :param log_level: Log level, defaults to ProcessLogLevel.Full
        :type log_level: ProcessLogLevel, optional
        :return: Instance of :class:`Process`, the process is already running.
        :rtype: ProcessType
        """
        if not isinstance(command, str):
            raise ValueError("Parameter command is not a string, did you mean async_exec() instead of async_run()?")

        self.connect()

        process = self.create_process(
            command=command,
            cwd=cwd,
            env=env,
            input=input,
            log_level=log_level,
            blocking_call=False,
        )

        process.run()
        return process

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
        input: str | None = None,
        log_level: ProcessLogLevel = ProcessLogLevel.Full,
        raise_on_error: bool = True,
    ) -> ProcessResultType:
        """
        Blocking command call.

        The command is run under shell specified in the constructor and it is
        executed immediately. It waits for the command to finish and returns its
        result.

        :param command: Command to run.
        :type command: str
        :param cwd: Working directory, defaults to None (= do not change)
        :type cwd: str | None, optional
        :param env: Additional environment variables, defaults to None
        :type env: dict[str, Any] | None, optional
        :param input: Content of standard input, defaults to None
        :type input: str | None, optional
        :param log_level: Log level, defaults to ProcessLogLevel.Full
        :type log_level: ProcessLogLevel, optional
        :param raise_on_error: If True, raise :class:`ProcessError` if command
            exited with non-zero return code, defaults to True
        :type raise_on_error: bool, optional
        :raises ProcessError: If ``raise_on_error`` is True and the command
            exited with non-zero return code.
        :return: Command result.
        :rtype: ProcessResultType
        """
        if not isinstance(command, str):
            raise ValueError("Parameter command is not a string, did you mean exec() instead of run()?")

        self.connect()

        process = self.create_process(
            command=command,
            cwd=cwd,
            env=env,
            input=input,
            log_level=log_level,
            blocking_call=True,
        )

        process.run()

        return process.wait(raise_on_error=raise_on_error)

    def async_exec(
        self,
        argv: list[Any],
        *,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
        input: str | None = None,
        log_level: ProcessLogLevel = ProcessLogLevel.Full,
    ) -> ProcessType:
        """
        Non-blocking command call.

        The command is run under shell specified in the constructor and it is
        executed immediately, however it does not wait for the command to
        finish.

        The command is provided as ``argv`` list.

        :param argv: Command to run.
        :type argv: list[Any]
        :param cwd: Working directory, defaults to None (= do not change)
        :type cwd: str | None, optional
        :param env: Additional environment variables, defaults to None
        :type env: dict[str, Any] | None, optional
        :param input: Content of standard input, defaults to None
        :type input: str | None, optional
        :param log_level: Log level, defaults to ProcessLogLevel.Full
        :type log_level: ProcessLogLevel, optional
        :return: Instance of :class:`Process`, the process is already running.
        :rtype: ProcessType
        """
        if not isinstance(argv, list):
            raise ValueError("Parameter argv is not a list, did you mean async_run() instead of async_exec()?")

        argv = [str(x) for x in argv]
        command = shlex.join(argv)

        return self.async_run(
            command,
            cwd=cwd,
            env=env,
            input=input,
            log_level=log_level,
        )

    def exec(
        self,
        argv: list[Any],
        *,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
        input: str | None = None,
        log_level: ProcessLogLevel = ProcessLogLevel.Full,
        raise_on_error: bool = True,
    ) -> ProcessResultType:
        """
        Blocking command call.

        The command is run under shell specified in the constructor and it is
        executed immediately. It waits for the command to finish and returns its
        result.

        The command is provided as ``argv`` list.

        :param argv: Command to run.
        :type argv: list[Any]
        :param cwd: Working directory, defaults to None (= do not change)
        :type cwd: str | None, optional
        :param env: Additional environment variables, defaults to None
        :type env: dict[str, Any] | None, optional
        :param input: Content of standard input, defaults to None
        :type input: str | None, optional
        :param log_level: Log level, defaults to ProcessLogLevel.Full
        :type log_level: ProcessLogLevel, optional
        :param raise_on_error: If True, raise :class:`ProcessError` if command
            exited with non-zero return code, defaults to True
        :type raise_on_error: bool, optional
        :raises ProcessError: If ``raise_on_error`` is True and the command
            exited with non-zero return code.
        :return: Command result.
        :rtype: ProcessResultType
        """
        if not isinstance(argv, list):
            raise ValueError("Parameter argv is not a list, did you mean run() instead of exec()?")

        argv = [str(x) for x in argv]
        command = shlex.join(argv)

        return self.run(
            command,
            cwd=cwd,
            env=env,
            input=input,
            log_level=log_level,
            raise_on_error=raise_on_error,
        )

    def expect(
        self,
        expect_script: str,
        *,
        verbose: bool = True,
        raise_on_error: bool = False,
    ) -> ProcessResultType:
        """
        Run expect script.

        :param expect_script: Expect script.
        :type expect_script: str
        :param verbose: Enable expect debug output (-d), default to True.
        :type verbose: bool, optional
        :param raise_on_error: If True, raise :class:`ProcessError` if command
            exited with non-zero return code, defaults to False
        :type raise_on_error: bool, optional
        :return: Expect script result.
        :rtype: ProcessResultType
        """
        args = ["-d"] if verbose else []
        return self.exec(["/bin/expect", *args], input=expect_script, raise_on_error=raise_on_error)

    def expect_nobody(
        self,
        expect_script: str,
        *,
        verbose: bool = True,
        raise_on_error: bool = False,
    ) -> ProcessResultType:
        """
        Run expect script as user nobody.

        The main use case is to avoid running the command as root if the client
        is connected to the root user SSH session.

        :param expect_script: Expect script.
        :type expect_script: str
        :param verbose: Enable expect debug output (-d), default to True.
        :type verbose: bool, optional
        :param raise_on_error: If True, raise :class:`ProcessError` if command
            exited with non-zero return code, defaults to False
        :type raise_on_error: bool, optional
        :return: Expect return code.
        :rtype: ProcessResultType
        """
        args = " -d" if verbose else ""
        return self.run(
            f'su --shell /bin/sh nobody -c "/bin/expect{args}"', input=expect_script, raise_on_error=raise_on_error
        )

    def __enter__(self) -> Connection:
        """
        Connect to the host.

        :return: Connection instance.
        :rtype: Connection
        """
        self.connect()
        return self

    def __exit__(self, exception_type, exception_value, traceback) -> None:
        """
        Disconnect.
        """
        self.disconnect()

    @classmethod
    @abstractmethod
    def from_confdict(cls, host: MultihostHost, confdict: dict[str, Any]) -> Self:
        """
        Create new instance of this class from configuration dictionary.

        :param host: Multihost host that will use this connection.
        :type host: MultihostHost
        :param confdict: Configuration dictionary.
        :type confdict: dict[str, Any]
        :return: New instance.
        :rtype: Self
        """
        pass


class Shell(ABC):
    """
    Multihost shell abstraction.
    """

    def __init__(self, name: str, shell_command: str) -> None:
        """
        :param name: Shell name.
        :type name: str
        :param shell_command: Shell command that will execute user scripts.
        :param shell_command: str
        """
        self.name: str = name
        """Shell name."""

        self.shell_command: str = shell_command
        """Shell command that will execute user scripts."""

    @abstractmethod
    def build_command_line(self, script: str, *, cwd: str | None, env: dict[str, Any]) -> str:
        """
        Create a complete command that will execute given script in this shell.

        User is able to specify a different working directory and additional
        environment variables. This method adds this information to the user's
        command and returns a full script that will switch the working
        directory, sets environment variables and then executes the given
        command or script.

        :param script: Script
        :type script: str
        :param cwd: Working directory, ``None`` means no change.
        :type cwd: str | None
        :param env: Additional environment variables.
        :type env: dict[str, Any]
        :return: Complete command line to run to execute the script in this
            shell.
        :rtype: str
        """
        pass


class Bash(Shell):
    """
    Bash shell abstraction.
    """

    def __init__(self) -> None:
        super().__init__("bash", "/usr/bin/bash -c")

    def build_command_line(self, script: str, *, cwd: str | None, env: dict[str, Any]) -> str:
        full_script = self._add_cwd_and_env(script, cwd=cwd, env=env)
        escaped_script = self._escape_single_quotes(full_script)

        return f"{self.shell_command} '{escaped_script}'"

    def _add_cwd_and_env(self, script: str, *, cwd: str | None, env: dict[str, Any]) -> str:
        out = ""

        # Set environment variables
        for key, value in env.items():
            out += f"export {key}={shlex.quote(str(value))}\n"

        # Set working directory
        if cwd is not None:
            out += f"cd {shlex.quote(cwd)}\n"

        if out:
            out += "\n"

        out += script

        return out

    def _escape_single_quotes(self, command: str) -> str:
        """
        We call the command as `bash -c '$command'`.

        We need to escape ' inside the script to make it work correctly.
        """
        return command.replace("'", "'\"'\"'")


class Powershell(Shell):
    """
    Powershell shell abstraction.
    """

    def __init__(self) -> None:
        super().__init__("powershell", "powershell -NonInteractive -Command")

    def build_command_line(self, script: str, *, cwd: str | None, env: dict[str, Any]) -> str:
        full_script = self._add_cwd_and_env(script, cwd=cwd, env=env)
        escaped_script = self._escape_quotes(full_script)

        return f"{self.shell_command} '{escaped_script}'"

    def _add_cwd_and_env(self, script: str, *, cwd: str | None, env: dict[str, Any]) -> str:
        out = ""

        # Set environment variables
        for key, value in env.items():
            out += f"$Env:{key} = {shlex.quote(str(value))}\n"

        # Set working directory
        if cwd is not None:
            out += f"cd {shlex.quote(cwd)}\n"

        if out:
            out += "\n"

        out += script

        return out

    def _escape_quotes(self, command: str) -> str:
        """
        We need to escape quotes inside the script to make it work correctly.
        """
        return command.replace("'", "''").replace('"', '\\"')
