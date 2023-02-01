from typing import Tuple, Dict, Union, Optional

from bilibili_api import Credential
from pydantic import BaseSettings, validator, EmailStr, SecretStr, AnyUrl


class Config(BaseSettings):
    # 全局设置
    fansub_senders: Tuple[str, ...] = ("",)
    fansub_masters: Tuple[str, ...] = ("",)
    fansub_groups: Tuple[int, ...] = (0,)  # 0号位群组用于debug时的推送，默认设置为0
    proxies: Optional[Union[AnyUrl, Dict[str, AnyUrl]]]
    debug: bool = False

    # 时间设置中的单位均为分钟

    # Mail推送功能
    mail: bool = False
    time_checkmailupdate: int = 5
    mail_recv_addr: EmailStr = ""
    mail_recv_pwd: SecretStr = ""
    pop3_server: str = "pop.qq.com"
    moni_addrs: Tuple[EmailStr, ...] = ("",)

    # 官方博客推送功能
    blog: bool = False
    time_checkblogupdate: int = 10
    member_abbr: str = "haruka.kaki"

    # B站发送动态功能（部分字段请参考bilibili_api）
    time_waitbeforesend: int = 10
    time_waitforimages: int = 2
    dynamic_topic: str = "#贺喜遥香#"
    bili_cred: Union[Credential, Dict[str, str]] = {"sessdata": "", "bili_jct": "", "buvid3": "", "dedeuserid": ""}

    # 官方推特推送功能（部分字段请参考Twitter API）
    tweet: bool = False
    time_checktweetupdate: int = 5
    tweet_moni_keywords: Tuple[str, ...] = ("賀喜遥香",)
    tweet_bearer_token: str = ""
    tweet_headers: Dict[str, str] = {}

    @validator("bili_cred")
    def cred_parser(cls, v):
        if v:
            if "sessdata" in v and "bili_jct" in v and "buvid3" in v:
                return Credential(**v)
            else:
                raise ValueError('must contain "sessdata", "bili_jct" and "buvid3"')

    def __init__(self, **data):
        super().__init__(**data)
        if isinstance(self.proxies, AnyUrl):
            self.proxies = {"all://": self.proxies}
        self.tweet_headers = {"Authorization": f"Bearer {self.tweet_bearer_token}", "User-Agent": "v2UserTweetsPython"}

    class Config:
        extra = "ignore"
