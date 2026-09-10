import glob
import os
import shutil
import subprocess

from openai import OpenAI
from apollo_lib import settings

_client = None

def _get_optional_setting(key, default=None):
  """Return an optional setting without exiting when the key is absent."""
  return settings.load_settings().get(key, default)


def _get_ai_provider():
  """Return the configured inference provider."""
  return str(_get_optional_setting("AI_PROVIDER", "openai")).strip().lower()


def _get_client():
  """Return a cached OpenAI client configured from settings."""
  global _client
  if _client is None:
    _client = OpenAI(
      base_url=settings.get_setting("OPENAI_BASE_URL"),
      api_key=settings.get_setting("OPENAI_API_KEY"),
      default_headers={
        "HTTP-Referer": "python",
        "X-Title": "python",
      }
    )
  return _client


def _ask_openai(request):
  """Send an OpenAI-compatible chat completion request and return the content."""
  client = _get_client()
  completion = client.chat.completions.create(
    model=settings.get_setting("OPENAI_MODEL"),
    messages=[
      {
        "role": "user",
        "content": request
      }
    ]
  )
  return completion.choices[0].message.content


def _resolve_codex_bin():
  """Return a runnable Codex binary path."""
  configured = _get_optional_setting("CODEX_BIN", "/home/jason/.local/bin/codex")
  if configured and os.access(configured, os.X_OK):
    return configured

  path_bin = shutil.which("codex")
  if path_bin:
    return path_bin

  candidates = sorted(glob.glob("/home/jason/.vscode/extensions/openai.chatgpt-*/bin/linux-x86_64/codex"))
  for candidate in reversed(candidates):
    if os.access(candidate, os.X_OK):
      return candidate

  raise RuntimeError(f"Codex binary is not executable: {configured}")


def _ask_codex(request):
  """Send a request through `codex exec` and return the final agent message."""
  codex_bin = _resolve_codex_bin()
  codex_workdir = _get_optional_setting("CODEX_WORKDIR", "/src/apollo")
  codex_home = _get_optional_setting("CODEX_HOME")
  timeout = int(_get_optional_setting("CODEX_TIMEOUT_SECONDS", 120))

  command = [
    codex_bin,
    "exec",
    "--ephemeral",
    "--sandbox",
    "read-only",
    "--color",
    "never",
    "--cd",
    codex_workdir,
  ]

  reasoning_effort = _get_optional_setting("CODEX_REASONING_EFFORT")
  if reasoning_effort:
    command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])

  codex_model = _get_optional_setting("CODEX_MODEL")
  if codex_model:
    command.extend(["--model", str(codex_model)])

  command.append("-")
  env = os.environ.copy()
  if codex_home:
    os.makedirs(codex_home, exist_ok=True)
    for filename in ["auth.json", "installation_id"]:
      source = os.path.join("/home/jason/.codex", filename)
      target = os.path.join(codex_home, filename)
      if os.path.exists(source) and not os.path.exists(target):
        os.symlink(source, target)
    env["CODEX_HOME"] = codex_home

  result = subprocess.run(
    command,
    input=request,
    text=True,
    capture_output=True,
    timeout=timeout,
    check=False,
    env=env,
  )

  if result.returncode != 0:
    details = result.stderr.strip() or result.stdout.strip()
    raise RuntimeError(f"Codex failed with exit code {result.returncode}: {details}")

  return result.stdout.strip()


def ask(request):
  """Send an inference request and return the content."""
  provider = _get_ai_provider()
  if provider == "openai":
    response = _ask_openai(request)
  elif provider == "codex":
    response = _ask_codex(request)
  else:
    raise ValueError(f"Unsupported AI_PROVIDER: {provider}")

  os.makedirs("/tmp/apollo", exist_ok=True)
  with open("/tmp/apollo/last_ai_restult.txt", "w") as f:
    f.write(response)

  return response


def get_playlist(description, length):
  """Generate an AI playlist given a description and length."""
  if not length:
    length = 50
  request = (
    "Only reply in plain text in the format 'artist - title', one song per line. "
    f"Write a playlist of {length} songs that fit the following request: " + description
  )
  response = ask(request)
  return response
