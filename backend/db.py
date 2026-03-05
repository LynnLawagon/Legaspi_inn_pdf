import mysql.connector

def get_conn():
    return mysql.connector.connect(
        host="127.0.0.1",
        user="root",
        password="",
        database="db_ocr_results"
    )