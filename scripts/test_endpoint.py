"""Simple endpoint smoke test against the configured OpenAI-compatible API."""
import sys
from openai import OpenAI, OpenAIError
from shared.config import PipelineConfig

def main() -> int:
    """Run a ping-pong completion request and print the model response."""
    print("User: 'Ping!'")
    client = OpenAI(
        base_url=PipelineConfig.OPENAI_API_BASE,
        api_key=PipelineConfig.OPENAI_API_KEY,
    )
    try:
        completion = client.chat.completions.create(
            model=PipelineConfig.OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "Ping! (Simply answer 'Pong!' if you've received this message)",
                },
            ],
        )
    except OpenAIError as error:
        print(f"OpenAI request failed: {error}")
        return 1
    print(f"Model: '{completion.choices[0].message.content}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
