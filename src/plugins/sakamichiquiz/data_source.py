import random
from typing import Union, Tuple, List, Optional

import nonebot
from mysql.connector import Error as MySQLError
from mysql.connector import MySQLConnection, OperationalError
from nonebot.log import logger

from .config import SQLConfig
from .model import Question

MAX_SQL_RETRY = 5


class SQLServer(object):
    def __init__(self):
        self.lastid = 0
        self.totl = 0
        self.conn: MySQLConnection

        self._sql_connect()
        # self.close()    # DEBUG ONLY
        self.total()

    def _execute(self, command: str, args: tuple):
        """
        Execute SQL command without retVal, such as INSERT, UPDATE,etc

        MySQLError will be raised when SQL server error occurs.

        :param command: The command string specified with WHERE command.
        :return: cursor.lastrowid when available.
        """
        while True:
            try:
                with self.conn.cursor() as cursor:
                    logger.debug("执行SQL命令（无返回值）：%s，参数：%s" % (command, args))
                    cursor.execute(command, args)
                    self.conn.commit()
                    if cursor.lastrowid:
                        self.lastid = cursor.lastrowid
                        logger.debug('末行行号：%s' % self.lastid)
                        return self.lastid
                return True
            except OperationalError:
                if self._sql_connect():
                    continue
                else:
                    raise MySQLError("数据库错误")

    def _select(self, command: str, args: Optional[Union[Tuple, List]] = None) -> Tuple:
        """
        Run SELECT command and get retVal.

        IndexError will be raised when get Empty retVal.
        MySQLError will be raised when SQL server error occurs.

        :param command: String with format like "SELECT * FROM TABLE_NAME WHERE CONDITION".
        :param args: Arguments used to replace placeholders in command.
        :return: Data Tuple / False when error occurs.
        """
        while True:
            try:
                with self.conn.cursor() as cursor:
                    logger.debug("执行SQL命令（有返回值）：%s，参数：%s" % (command, args))
                    cursor.execute(command, args)
                    ret = cursor.fetchone()
                if not ret:
                    raise IndexError("数据库返回的数据为空")
                return ret
            except OperationalError:
                if self._sql_connect():
                    continue
                else:
                    raise MySQLError("数据库错误")

    def _cursor_fetchbyid(self, recordid: int):
        """
        Fetch record of one row from SQL table(questions) by id.

        IndexError will be raised when get Empty retVal.
        MySQLError will be raised when SQL server error occurs.

        :param recordid: Record id
        :return: Data tuple
        """
        if not (isinstance(recordid, int)):
            raise IndexError
        select = "SELECT * FROM questions " \
                 "WHERE id = %s"
        logger.debug("获取SQL数据（有返回值）：%s，参数：%s" % (select, recordid))
        ret = self._select(select, (recordid,))
        return ret

    @staticmethod
    def _parse2question(raw: tuple) -> Question:
        dic = {
            "id": raw[0],
            "typ": raw[1],
            "content": raw[2],
            "files": raw[3],
            "answers": raw[4],
            "analysis": raw[5],
            "analfiles": raw[6],
            "author": raw[7],
            "countright": raw[8],
            "counttotal": raw[9],
            "sakagroup": raw[10],
            "if_err": raw[11],
            "create_time": raw[12],
        }
        return Question.parse_obj(dic)

    def _sql_connect(self) -> bool:
        """ Connect to MySQL database """
        global_config = nonebot.get_driver().config
        db_config = SQLConfig(**global_config.dict()).sqldict()
        conn = None
        retry = MAX_SQL_RETRY
        while retry:
            try:
                logger.info('正在连接MySQL题库数据库...')
                conn = MySQLConnection(**db_config)

                if conn.is_connected():
                    logger.info('成功建立与数据库的连接')
                    self.conn = conn
                    return True
                else:
                    logger.error(f'数据库连接失败，第{MAX_SQL_RETRY - retry}次重试...')
                    retry = retry - 1
            except MySQLError as error:
                logger.error(error)
            return False
        return False

    def new(self, question: Question):
        insert = "INSERT INTO questions" \
                 "(typ, content, answers, files, analysis, analfiles, author, sakagroup, if_err)" \
                 "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        args = (int(question.typ), question.content, question.answers, question.files, question.analysis,
                question.analfiles, question.author, int(question.sakagroup), question.if_err)
        lastid = self._execute(insert, args)
        return lastid

    def remove(self, recordid: int):
        """
        Move record identified by id from table[questions] to table[questions_bak]
        :param recordid:
        :return: Successfully moved record.
        """
        record = self._cursor_fetchbyid(recordid)  # get data which will be deleted.

        insert = "INSERT INTO questions_bak" \
                 "(id, typ, content, files, answers, analysis, analfiles, author, " \
                 "countright, counttotal, sakagroup, if_err, create_time)" \
                 "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        flag1 = self._execute(insert, record)

        delete = "DELETE FROM questions WHERE id = %s"
        flag2 = self._execute(delete, (recordid,))
        if flag1 and flag2:
            return True
        else:
            return False

    def edit(self, recordid: int, record: dict):
        """
        Edit record in TABLE questions.

        IndexError will be raised when get empty retval OR invalid recordid.

        :param recordid:
        :param record: Dict of keys&values need to be updated
        :return:
        """
        old = self._cursor_fetchbyid(recordid)  # check record if exists in table.
        update = "UPDATE questions SET "
        keystr = ""
        args = []
        for key, value in record.items():  # Placeholders can be all '%s' whether type is int or str.
            if key in ['content', 'answers', 'files', 'analysis', 'analfiles',
                       'author', "countright", "counttotal", 'if_err']:
                keystr = keystr + str(key) + "= %s,"
                args.append(value)
            if key in ['typ', 'sakagroup']:
                keystr = keystr + str(key) + "= %s,"
                args.append(str(int(value)))
        keystr = keystr[0:-1]  # skip comma at end.(",")
        args.append(recordid)
        update = update + keystr + " WHERE id = %s"

        ret = self._execute(update, tuple(args))
        return ret

    def query(self, recordid: int) -> Question:
        record = self._cursor_fetchbyid(recordid)
        question = self._parse2question(record)
        return question

    def close(self):
        if self.conn is not None and self.conn.is_connected():
            self.conn.close()
            logger.info('与数据库的连接已断开')

    def total(self):
        """
        Get total of valid records and update lastid in SQL table(questions)
        """
        select = "SELECT COUNT(*) FROM questions"
        select2 = "SELECT id FROM questions ORDER BY id DESC LIMIT 1;"
        try:
            ret = self._select(select)
            self.totl = ret[0]
            ret = self._select(select2)
            self.lastid = ret[0]
            logger.info("题库总题数：%s，最新题目序号：%s" % (self.totl, self.lastid))
        except IndexError as errmsg:
            logger.error(errmsg)

    def update_user_counter(self, qq: int, state: bool):
        select = f"SELECT * FROM users WHERE qq={qq}"
        try:
            ret = self._select(select)
        except IndexError:
            ret = (qq, 0, 0)
            new = "INSERT INTO users (qq, correct, total) VALUES(%s, %s, %s)"
            self._execute(new, ret)
        ret = list(ret)
        ret[1] = ret[1] + 1 if state else ret[1]
        ret[2] = ret[2] + 1
        update = "UPDATE users SET correct=%s, total=%s WHERE qq=%s"
        self._execute(update, (ret[1], ret[2], ret[0]))

    def update_user_skiptype(self, qq: str, liststr: str):
        select = f"SELECT * FROM users WHERE qq={qq}"
        update = f"UPDATE users SET skiptype=%s WHERE qq={qq}"
        try:
            ret = self._select(select)
        except IndexError:
            ret = (qq, 0, 0)
            new = "INSERT INTO users (qq, correct, total) VALUES(%s, %s, %s)"
            self._execute(new, ret)
        self._execute(update, (liststr,))

    def get_user_info(self, qq: str) -> tuple:
        select = f"SELECT * FROM users WHERE qq={qq}"
        ret = self._select(select)

        select2 = f"""select total_ranks, correct_ranks from 
                    (select rank() over (order by total desc) as total_ranks, qq from users)
                    as A,
                    (select rank() over (order by (correct/total) desc) as correct_ranks, qq from users where total>20)
                    as B 
                    where A.qq={qq} and B.qq={qq};
                    """
        try:
            ret2 = self._select(select2)
        except IndexError:
            ret2 = ("-", "-")

        return ret + ret2

    def get_latest_question_id(self):
        select = "SELECT id from questions where counttotal=0"
        while True:
            try:
                with self.conn.cursor() as cursor:
                    cursor.execute(select)
                    ret = cursor.fetchall()
                if not ret:
                    logger.info("没有尚未作答的题目了")
                    return -1
                rd = random.randint(0, len(ret) - 1)
                logger.info(f"抽取未被答过的新题，题目序号：{ret[rd][0]}")
                return ret[rd][0]
            except OperationalError:
                if self._sql_connect():
                    continue
                else:
                    raise MySQLError("数据库错误")
