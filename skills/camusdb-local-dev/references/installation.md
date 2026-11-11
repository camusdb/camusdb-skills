# Server installation reference

Source: https://camusdb.github.io/docs/server-installation

All three methods run the same server and the same SQL APIs. Each starts a standalone node with
REST on port `5095` and gRPC on `5096`. Stop an existing node before you start another on the
same ports.

| Method | Requirements | Choose it when |
| --- | --- | --- |
| NuGet tool | .NET 10 SDK to install the tool | You already use .NET and want a global `camusdb` command. |
| Homebrew | Homebrew on macOS or Linux | You want a native command or background service with its runtime included. |
| Docker | A running Docker engine | You want a container with data kept in a named volume. |

## NuGet tool

```bash
dotnet tool install --global CamusDB.Server
camusdb
```

- With no configuration file, the node uses built-in defaults and stores data in the user data
  directory.
- `camusdb init` creates a starter configuration and the default data directory. Edit the file,
  then restart `camusdb`.
- Stop a foreground node with Ctrl+C.

Update: stop the node, update the tool, and start it again.

```bash
dotnet tool update --global CamusDB.Server
camusdb
```

## Homebrew

The package includes the .NET runtime. The server needs no separate .NET installation.

```bash
brew install camusdb/tap/camusdb
camusdb                          # foreground, with your user configuration; Ctrl+C stops it
```

Run a background service instead:

```bash
brew services start camusdb
brew services stop camusdb
brew services restart camusdb
```

- The service uses its own configuration and data directory under `$(brew --prefix)/var/camusdb`.
  It does not use the foreground command's user configuration.
- Homebrew creates the service configuration at install. It keeps your edits on upgrade.

Update the package, then restart the service to load the new version:

```bash
brew upgrade camusdb
brew services restart camusdb
```

If you run the foreground command, stop it before the upgrade and run `camusdb` again after it.

## Docker

```bash
docker run --rm \
  -p 5095:5095 \
  -p 5096:5096 \
  -v camus-data:/data \
  --name camusdb camusdb/camusdb:latest
```

- The image includes the runtime.
- The `camus-data` volume keeps database files under `/data`. `--rm` removes the stopped
  container; the named volume stays for the next run.
- Stop the node with Ctrl+C, or `docker stop camusdb` from another terminal.
- Update: stop the container, run `docker pull camusdb/camusdb:latest`, then start it again with
  the same volume.
- Use a version tag in place of `latest` for a fixed server version.

## Check the node

```bash
curl --fail http://localhost:5095/health
```

To run SQL, install `camus-cli` or the Web Console. Both are separate installations and need the
.NET 10 SDK, also when the server runs through Homebrew or Docker. A database must be created
explicitly before use.

## Configuration and data locations

Defaults. Explicit configuration and environment settings can override them.

| Installation | Configuration | Data |
| --- | --- | --- |
| Foreground command on macOS/Linux | `~/.camusdb/config.yml`, created by `camusdb init` | `$XDG_DATA_HOME/camusdb`, or `~/.local/share/camusdb` when unset |
| NuGet tool on Windows | `%APPDATA%\camusdb\config.yml`, created by `camusdb init` | `%LOCALAPPDATA%\camusdb` |
| Homebrew service | `$(brew --prefix)/var/camusdb/config.yml` | `$(brew --prefix)/var/camusdb/data` |
| Docker command above | Built-in or image settings unless overridden | `/data`, backed by the `camus-data` volume |

- The Homebrew service writes its log to `$(brew --prefix)/var/log/camusdb.log`.
- A foreground process or a Docker container writes to its console. Use `docker logs camusdb`
  while the container exists.
- For installed commands, `CAMUS_HOME` moves the user configuration to `$CAMUS_HOME/config.yml`
  and the default data directory to `$CAMUS_HOME/data`.
- `--config <path>` selects a configuration file explicitly. See `configuration.md` for the
  discovery order and precedence.

## Run a cluster

The commands above run one standalone node. For a local cluster, use the Aspire example or the
manual setup in `local-cluster.md`. For Kubernetes, use the Kubernetes Operator.
