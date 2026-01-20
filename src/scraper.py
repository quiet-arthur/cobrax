import os

from dotenv import load_dotenv
from config import BASE_URL

load_dotenv()

print(BASE_URL)
print(os.getenv("LOGIN_USER"))
print(os.getenv("ALPHA_PASSWORD_HASH"))