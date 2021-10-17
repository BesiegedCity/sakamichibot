from typing import Tuple

from pydantic import BaseSettings, DirectoryPath, SecretStr


class Config(BaseSettings):
    # 问答超时时间，单位为秒
    qna_timeout: int = 80
    # 最多可以同时进行的问答过程数
    qna_groupmax: int = 20
    # 出题者群组
    qna_supergroups: Tuple[int, ...] = ()
    # 成功跳过偏向跳过题目的概率
    qna_skip_possibility: int = 75
    qna_imagecatagory: DirectoryPath = ""
    qna_musiccatagory: DirectoryPath = ""

    class Config:
        extra = "ignore"


class SQLConfig(BaseSettings):
    # 设置MySQL数据库地址，数据库名，登录用户名与密码
    sql_host: str = ""
    sql_database: str = ""
    sql_user: str = ""
    sql_password: SecretStr = ""

    def sqldict(self):
        d = {
            "host": self.sql_host,
            "database": self.sql_database,
            "user": self.sql_user,
            "password": self.sql_password.get_secret_value(),
        }
        return d

    class Config:
        extra = "ignore"
