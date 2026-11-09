import os

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-characters-long-example!!"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"

