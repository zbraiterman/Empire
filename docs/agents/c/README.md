# C Agent Overview

The C agent (`Cpire`) is an experimental native agent for Empire, modeled after the Go agent (Gopire). It compiles into a standalone binary and supports Windows and Linux targets via cross-compilation. **Currently, the C agent supports the HTTP listener only.**

The C agent is only available in the [Sponsors](https://github.com/sponsors/BC-SECURITY) version of Empire.

## Prerequisites

To compile the C agent, the following are required:

- A C compiler (GCC or MinGW for cross-compilation)
- OpenSSL development libraries
- For Windows targets: `x86_64-w64-mingw32-gcc` (MinGW-w64 toolchain)

## Compilation and Setup

The C agent is compiled server-side via the `CCompiler` class. When generating a stager through the `multi/c_exe` stager, the server handles compilation automatically using the configured compiler.

### Stager Options

| Option | Description | Default |
|--------|-------------|---------|
| `Listener` | Listener to use (required) | - |
| `CC` | C compiler override | `x86_64-w64-mingw32-gcc` (Windows) or `gcc` (Linux) |
| `CFLAGS` | Extra compiler flags | - |
| `TargetOS` | Target OS: `windows` or `linux` | `windows` |

### Manual Compilation (Outside Empire)

```bash
# Windows target (cross-compile from Linux)
x86_64-w64-mingw32-gcc -o cpire.exe main.c agent/*.c comms/*.c crypto/*.c tasks/*.c common/*.c profile/*.c \
  -I. -Iagent -Icomms -Icrypto -Itasks -Icommon -Iprofile \
  -I/opt/openssl-mingw64/include -L/opt/openssl-mingw64/lib64 \
  -lssl -lcrypto -lws2_32 -lwinhttp -lcrypt32 -lole32 -loleaut32 -ladvapi32 -static

# Linux target
gcc -o cpire main.c agent/*.c comms/*.c crypto/*.c tasks/*.c common/*.c profile/*.c \
  -I. -Iagent -Icomms -Icrypto -Itasks -Icommon -Iprofile \
  -lssl -lcrypto -lcurl
```

## Features

- **Cross-platform**: Supports Windows and Linux targets.
- **Native binary**: Compiles to a standalone executable with no runtime dependencies (statically linked on Windows).
- **Full staging**: DH key exchange with Ed25519 certificate verification, AES-CBC encrypt-then-HMAC session keys, and ChaCha20-Poly1305 routing packets.
- **Encrypted comms**: HTTP/HTTPS via WinHTTP (Windows) or libcurl (Linux).
- **Task execution**: Shell commands, PowerShell, C#/.NET assembly loading, and BOF (Beacon Object File) execution.
- **File operations**: File download (chunked), upload, and JSON directory listing.
- **Agent controls**: Delay/jitter, kill date, working hours, and lost-limit enforcement.
- **OPSEC**: No debug output in production builds. Debug logging available via `-DCPIRE_DEBUG` compile flag.
- **HTTP listener support**: Only supports the HTTP listener for communication.

## Supported Tasks

| Task ID | Name | Description |
|---------|------|-------------|
| 1 | SYSINFO | Collect system information |
| 2 | EXIT | Terminate the agent |
| 10/12 | SET/GET_DELAY | Set or query delay and jitter |
| 30/31 | SET/GET_KILLDATE | Set or query kill date |
| 32/33 | SET/GET_WORKING_HOURS | Set or query working hours |
| 40 | SHELL | Execute a shell command |
| 41 | DOWNLOAD | Download a file from target (chunked) |
| 42 | UPLOAD | Upload a file to target |
| 43 | DIR_LIST | List directory contents (JSON) |
| 100 | POWERSHELL | Execute PowerShell script (wait for output) |
| 101 | POWERSHELL_CMD_WAIT | Execute PowerShell with save-file prefix |
| 102 | POWERSHELL_CMD_JOB | Execute PowerShell (background) |
| 120 | CSHARP_CMD_WAIT | Load and execute .NET assembly |
| 121 | CSHARP_CMD_WAIT (save) | Load .NET assembly with save-file prefix |
| 122 | CSHARP_CMD_JOB | Load .NET assembly (background) |
| 123 | CSHARP_CMD_JOB (save) | Load .NET assembly background with save-file |
| 130 | BOF_CMD_WAIT | Execute Beacon Object File |

## Security

The `CCompiler` class validates compiler flags and compiler binaries against an allowlist to prevent command injection during server-side compilation. The agent zeros all cryptographic key material using `OPENSSL_cleanse` before freeing memory to prevent forensic recovery.
