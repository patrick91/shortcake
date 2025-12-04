# Config Command

The `config` command allows you to manage shortcake configuration settings. Configuration is stored in a TOML file following the XDG Base Directory specification.

## Command Syntax

```bash
sc config <action> [key] [value]
```

## Actions

### `list`
List all current configuration settings and show the configuration file location.

```bash
sc config list
```

### `get`
Retrieve the value of a specific configuration key.

```bash
sc config get <key>
```

### `set`
Update the value of a configuration key.

```bash
sc config set <key> <value>
```

## Configuration Options

### `keep_emoji`

**Type:** Boolean
**Default:** `false`
**Description:** Controls whether emojis should be preserved in branch names when creating branches from issue titles.

**Accepted values:**
- `true`, `1`, `yes` - Keep emojis in branch names
- `false`, `0`, `no` - Remove emojis from branch names

## Algorithm

The config command follows a simple flow based on the action specified:

### List Action

1. Load the configuration from the config file (or defaults if file doesn't exist)
2. Iterate through all configuration fields and display them
3. Show the configuration file path

### Get Action

1. Validate that a key was provided (exit with error if not)
2. Load the configuration from the config file
3. Check if the key exists in the configuration
4. If found, display the key and its value
5. If not found, display available keys

### Set Action

1. Validate that both key and value were provided (exit with error if not)
2. Check if the key is a recognized configuration option
3. For boolean keys like `keep_emoji`:
   - Parse the value as a boolean (`true/1/yes` or `false/0/no`)
   - Exit with error if the value is invalid
4. Save the updated configuration to the config file
5. Display confirmation of the change

**Note:** Currently only `keep_emoji` is supported. Attempting to set other keys will result in an error listing available keys.

## Configuration File Location

The configuration file follows the XDG Base Directory specification:

- If `XDG_CONFIG_HOME` is set: `$XDG_CONFIG_HOME/shortcake/config.toml`
- Otherwise: `~/.config/shortcake/config.toml`

The file is in TOML format and is automatically created when you first set a configuration value.

## Examples

### List all configuration

```bash
sc config list
```

Output:
```
Current configuration:
  keep_emoji = false

Configuration file: /Users/username/.config/shortcake/config.toml
```

### Get a specific setting

```bash
sc config get keep_emoji
```

Output:
```
keep_emoji = false
```

### Enable emoji preservation

```bash
sc config set keep_emoji true
```

Output:
```
Set keep_emoji = true
```

### Disable emoji preservation

```bash
sc config set keep_emoji false
```

Output:
```
Set keep_emoji = false
```

### Attempt to get an unknown key

```bash
sc config get unknown_key
```

Output:
```
Configuration key 'unknown_key' not found
Available keys: keep_emoji
```

### Attempt to set an unknown key

```bash
sc config set unknown_key value
```

Output:
```
Error: Unknown configuration key 'unknown_key'
Available keys: keep_emoji
```

## Implementation Details

The configuration system is implemented using:

- **Pydantic models** (`ShortcakeConfig`) for type-safe configuration with validation
- **rtoml** for reading and writing TOML files with pretty formatting
- **XDG Base Directory specification** for standard config file location

The configuration is lazily loaded when needed and saved immediately when changed through the `set` action.
