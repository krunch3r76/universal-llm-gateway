'use strict';

// Loaded via NODE_OPTIONS=--require for the cursor-sdk bridge only.
// On load, drop that require and the fallback cwd from process.env so Node
// children (and the bash they spawn) do not inherit the wrapper.

const childProcess = require('child_process');
const fs = require('fs');

const preloadPath = __filename;
const fallbackCwd = process.env.CURSOR_SDK_SHELL_FALLBACK_CWD || '';

function sameFile(left, right) {
  if (!left || !right) {
    return false;
  }
  if (left === right) {
    return true;
  }
  try {
    return fs.realpathSync(left) === fs.realpathSync(right);
  } catch (_err) {
    return false;
  }
}

function stripOwnRequire(nodeOptions) {
  const tokens = String(nodeOptions || '').split(/\s+/).filter(Boolean);
  const kept = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token === '--require' && sameFile(tokens[i + 1], preloadPath)) {
      i += 1;
      continue;
    }
    const prefixed = '--require=';
    if (token.startsWith(prefixed) && sameFile(token.slice(prefixed.length), preloadPath)) {
      continue;
    }
    kept.push(token);
  }
  return kept.join(' ');
}

const remaining = stripOwnRequire(process.env.NODE_OPTIONS);
if (remaining) {
  process.env.NODE_OPTIONS = remaining;
} else {
  delete process.env.NODE_OPTIONS;
}
delete process.env.CURSOR_SDK_SHELL_FALLBACK_CWD;

function isBash(command) {
  return typeof command === 'string' && (command === 'bash' || command.endsWith('/bash'));
}

function isDir(dirPath) {
  try {
    return fs.statSync(dirPath).isDirectory();
  } catch (_err) {
    return false;
  }
}

const originalSpawn = childProcess.spawn;

function wrappedSpawn(command, args, options) {
  let argv = args;
  let opts = options;
  if (args != null && !Array.isArray(args) && typeof args === 'object') {
    opts = args;
    argv = undefined;
  }
  const cwd = opts && typeof opts.cwd === 'string' ? opts.cwd : '';
  if (isBash(command) && cwd && !isDir(cwd)) {
    process.stderr.write(`shell_cwd_missing ${cwd}\n`);
    if (fallbackCwd && isDir(fallbackCwd)) {
      const copied = Object.assign({}, opts);
      copied.cwd = fallbackCwd;
      if (argv === undefined) {
        return originalSpawn.call(this, command, copied);
      }
      return originalSpawn.call(this, command, argv, copied);
    }
  }
  return originalSpawn.apply(this, arguments);
}

childProcess.spawn = wrappedSpawn;
