import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway/src'))
from chatbridge.security import protect
# 供父进程通过匿名管道读取，切勿在终端直接运行或重定向到明文文件。
sys.stdout.write(protect(Path(sys.argv[1]).read_bytes(),True).decode())
