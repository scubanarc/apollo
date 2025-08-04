from openai import OpenAI
from apollo_lib import settings

_client = None

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


def ask(request):
  """Send a chat completion request and return the content."""
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
