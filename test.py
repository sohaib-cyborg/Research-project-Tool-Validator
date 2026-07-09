import os
import openai
from dotenv import load_dotenv  
load_dotenv()
client = openai.OpenAI(
    base_url="https://llm.scads.ai/v1",
    api_key=os.environ["SCADSAI_API_KEY"],
)

# First: list models to confirm auth + endpoint work
print("Models available:")
for m in client.models.list().data:
    print(" ", m.id)

# Then: one real completion
resp = client.chat.completions.create(
    model="meta-llama/Llama-3.3-70B-Instruct",
    messages=[{"role": "user", "content": "Reply with exactly: SCADS OK"}],
    max_tokens=10,
)
print("\nResponse:", resp.choices[0].message.content)