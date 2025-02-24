# Databricks notebook source
# MAGIC %pip install --quiet -U databricks-sdk==0.23.0 langchain-community==0.2.10 langchain-openai==0.1.19 mlflow==2.14.3 faker
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ../_resources/00-init $reset_all=false

# COMMAND ----------

# MAGIC %md 
# MAGIC # Leveraging Genie as a tool

# COMMAND ----------

# databricks_token = dbutils.secrets.get('dbdemos', 'llm-agent-tools')
databricks_token = '<token>' #gitleaks:allow

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ## Strategy
# MAGIC
# MAGIC You will have a base function called _genie_query which will accept the `databricks_host`, `databricks_token`, `space_id`, `question` and `contextual_history`. This is not the function that will be used by end user. We will not wrap this into another SQL Function using one of the strategies below.
# MAGIC
# MAGIC
# MAGIC
# MAGIC ### One hosted function per room and let the llm route to the appropraite tool or multiple tools
# MAGIC
# MAGIC Take a look at the following example code
# MAGIC
# MAGIC ```sql
# MAGIC CREATE OR REPLACE FUNCTION chat_with_sales(question STRING COMMENT "the question to ask about sales data",
# MAGIC                   contextual_history STRING COMMENT "provide relavant history to be able to answer this question, assume genie doesnt keep track of history. Use 'no relevant history' if there is nothing relevant to answer the question.")
# MAGIC RETURNS STRING
# MAGIC LANGUAGE SQL
# MAGIC COMMENT 'This is a agent that you can converse with to get answers to questions about sales. Try to provide simple questions and provide history if you had prior conversations.' 
# MAGIC AS
# MAGIC SELECT _genie_query(
# MAGIC   secrets("genie_scope", "databricks_host_key"),
# MAGIC   secrets("genie_scope", "databricks_host_token"),
# MAGIC   '<space_id>',
# MAGIC   question, -- retrieved from function
# MAGIC   contextual_history -- retrieved from function
# MAGIC )
# MAGIC
# MAGIC ```
# MAGIC
# MAGIC
# MAGIC ### Generic hosted function with space_id as an argument with details on which spaces to use. (tbd)
# MAGIC

# COMMAND ----------

# MAGIC %sql
# MAGIC -- USE CATALOG main;
# MAGIC -- USE SCHEMA sri_genie_demo;
# MAGIC use catalog yevtushenko_artem;
# MAGIC use schema dbdemos_agent_tools;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION _genie_query(databricks_host STRING, 
# MAGIC                   databricks_token STRING,
# MAGIC                   space_id STRING,
# MAGIC                   question STRING,
# MAGIC                   contextual_history STRING)
# MAGIC RETURNS STRING
# MAGIC LANGUAGE PYTHON
# MAGIC COMMENT 'This is a agent that you can converse with to get answers to questions. Try to provide simple questions and provide history if you had prior conversations.'
# MAGIC AS
# MAGIC $$
# MAGIC     import json
# MAGIC     import os
# MAGIC     import time
# MAGIC     from dataclasses import dataclass
# MAGIC     from datetime import datetime
# MAGIC     from typing import Optional
# MAGIC     
# MAGIC     import pandas as pd
# MAGIC     import requests
# MAGIC     
# MAGIC     
# MAGIC     @dataclass
# MAGIC     class GenieResult:
# MAGIC         space_id: str
# MAGIC         conversation_id: str
# MAGIC         question: str
# MAGIC         content: Optional[str]
# MAGIC         sql_query: Optional[str] = None
# MAGIC         sql_query_description: Optional[str] = None
# MAGIC         sql_query_result: Optional[pd.DataFrame] = None
# MAGIC         error: Optional[str] = None
# MAGIC     
# MAGIC         def to_json_results(self):
# MAGIC             result = {
# MAGIC                 "space_id": self.space_id,
# MAGIC                 "conversation_id": self.conversation_id,
# MAGIC                 "question": self.question,
# MAGIC                 "content": self.content,
# MAGIC                 "sql_query": self.sql_query,
# MAGIC                 "sql_query_description": self.sql_query_description,
# MAGIC                 "sql_query_result": self.sql_query_result.to_dict(
# MAGIC                     orient="records") if self.sql_query_result is not None else None,
# MAGIC                 "error": self.error,
# MAGIC             }
# MAGIC             jsonified_results = json.dumps(result)
# MAGIC             return f"Genie Results are: {jsonified_results}"
# MAGIC     
# MAGIC         def to_string_results(self):
# MAGIC             results_string = self.sql_query_result.to_dict(orient="records") if self.sql_query_result is not None else None
# MAGIC             return ("Genie Results are: \n"
# MAGIC                     f"Space ID: {self.space_id}\n"
# MAGIC                     f"Conversation ID: {self.conversation_id}\n"
# MAGIC                     f"Question That Was Asked: {self.question}\n"
# MAGIC                     f"Content: {self.content}\n"
# MAGIC                     f"SQL Query: {self.sql_query}\n"
# MAGIC                     f"SQL Query Description: {self.sql_query_description}\n"
# MAGIC                     f"SQL Query Result: {results_string}\n"
# MAGIC                     f"Error: {self.error}")
# MAGIC     
# MAGIC     class GenieClient:
# MAGIC     
# MAGIC         def __init__(self, *,
# MAGIC                      host: Optional[str] = None,
# MAGIC                      token: Optional[str] = None,
# MAGIC                      api_prefix: str = "/api/2.0/genie/spaces"):
# MAGIC             self.host = host or os.environ.get("DATABRICKS_HOST")
# MAGIC             self.token = token or os.environ.get("DATABRICKS_TOKEN")
# MAGIC             assert self.host is not None, "DATABRICKS_HOST is not set"
# MAGIC             assert self.token is not None, "DATABRICKS_TOKEN is not set"
# MAGIC             self._workspace_client = requests.Session()
# MAGIC             self._workspace_client.headers.update({"Authorization": f"Bearer {self.token}"})
# MAGIC             self._workspace_client.headers.update({"Content-Type": "application/json"})
# MAGIC             self.api_prefix = api_prefix
# MAGIC             self.max_retries = 300
# MAGIC             self.retry_delay = 1
# MAGIC             self.new_line = "\r\n"
# MAGIC     
# MAGIC         def _make_url(self, path):
# MAGIC             return f"{self.host.rstrip('/')}/{path.lstrip('/')}"
# MAGIC     
# MAGIC         def start(self, space_id: str, start_suffix: str = "") -> str:
# MAGIC             path = self._make_url(f"{self.api_prefix}/{space_id}/start-conversation")
# MAGIC             resp = self._workspace_client.post(
# MAGIC                 url=path,
# MAGIC                 headers={"Content-Type": "application/json"},
# MAGIC                 json={"content": "starting conversation" if not start_suffix else f"starting conversation {start_suffix}"},
# MAGIC             )
# MAGIC             resp = resp.json()
# MAGIC             print(resp)
# MAGIC             return resp["conversation_id"]
# MAGIC     
# MAGIC         def ask(self, space_id: str, conversation_id: str, message: str) -> GenieResult:
# MAGIC             path = self._make_url(f"{self.api_prefix}/{space_id}/conversations/{conversation_id}/messages")
# MAGIC             # TODO: cleanup into a separate state machine
# MAGIC             resp_raw = self._workspace_client.post(
# MAGIC                 url=path,
# MAGIC                 headers={"Content-Type": "application/json"},
# MAGIC                 json={"content": message},
# MAGIC             )
# MAGIC             resp = resp_raw.json()
# MAGIC             message_id = resp.get("message_id", resp.get("id"))
# MAGIC             if message_id is None:
# MAGIC                 print(resp, resp_raw.url, resp_raw.status_code, resp_raw.headers)
# MAGIC                 return GenieResult(content=None, error="Failed to get message_id")
# MAGIC     
# MAGIC             attempt = 0
# MAGIC             query = None
# MAGIC             query_description = None
# MAGIC             content = None
# MAGIC     
# MAGIC             while attempt < self.max_retries:
# MAGIC                 resp_raw = self._workspace_client.get(
# MAGIC                     self._make_url(f"{self.api_prefix}/{space_id}/conversations/{conversation_id}/messages/{message_id}"),
# MAGIC                     headers={"Content-Type": "application/json"},
# MAGIC                 )
# MAGIC                 resp = resp_raw.json()
# MAGIC                 status = resp["status"]
# MAGIC                 if status == "COMPLETED":
# MAGIC                     try:
# MAGIC     
# MAGIC                         query = resp["attachments"][0]["query"]["query"]
# MAGIC                         query_description = resp["attachments"][0]["query"].get("description", None)
# MAGIC                         content = resp["attachments"][0].get("text", {}).get("content", None)
# MAGIC                     except Exception as e:
# MAGIC                         return GenieResult(
# MAGIC                             space_id=space_id,
# MAGIC                             conversation_id=conversation_id,
# MAGIC                             question=message,
# MAGIC                             content=resp["attachments"][0].get("text", {}).get("content", None)
# MAGIC                         )
# MAGIC                     break
# MAGIC     
# MAGIC                 elif status == "EXECUTING_QUERY":
# MAGIC                     self._workspace_client.get(
# MAGIC                         self._make_url(
# MAGIC                             f"{self.api_prefix}/{space_id}/conversations/{conversation_id}/messages/{message_id}/query-result"),
# MAGIC                         headers={"Content-Type": "application/json"},
# MAGIC                     )
# MAGIC                 elif status in ["FAILED", "CANCELED"]:
# MAGIC                     return GenieResult(
# MAGIC                         space_id=space_id,
# MAGIC                         conversation_id=conversation_id,
# MAGIC                         question=message,
# MAGIC                         content=None,
# MAGIC                         error=f"Query failed with status {status}"
# MAGIC                     )
# MAGIC                 elif status != "COMPLETED" and attempt < self.max_retries - 1:
# MAGIC                     time.sleep(self.retry_delay)
# MAGIC                 else:
# MAGIC                     return GenieResult(
# MAGIC                         space_id=space_id,
# MAGIC                         conversation_id=conversation_id,
# MAGIC                         question=message,
# MAGIC                         content=None,
# MAGIC                         error=f"Query failed or still running after {self.max_retries * self.retry_delay} seconds"
# MAGIC                     )
# MAGIC                 attempt += 1
# MAGIC             resp = self._workspace_client.get(
# MAGIC                 self._make_url(
# MAGIC                     f"{self.api_prefix}/{space_id}/conversations/{conversation_id}/messages/{message_id}/query-result"),
# MAGIC                 headers={"Content-Type": "application/json"},
# MAGIC             )
# MAGIC             resp = resp.json()
# MAGIC             columns = resp["statement_response"]["manifest"]["schema"]["columns"]
# MAGIC             header = [str(col["name"]) for col in columns]
# MAGIC             rows = []
# MAGIC             output = resp["statement_response"]["result"]
# MAGIC             if not output:
# MAGIC                 return GenieResult(
# MAGIC                     space_id=space_id,
# MAGIC                     conversation_id=conversation_id,
# MAGIC                     question=message,
# MAGIC                     content=content,
# MAGIC                     sql_query=query,
# MAGIC                     sql_query_description=query_description,
# MAGIC                     sql_query_result=pd.DataFrame([], columns=header),
# MAGIC                 )
# MAGIC             for item in resp["statement_response"]["result"]["data_typed_array"]:
# MAGIC                 row = []
# MAGIC                 for column, value in zip(columns, item["values"]):
# MAGIC                     type_name = column["type_name"]
# MAGIC                     str_value = value.get("str", None)
# MAGIC                     if str_value is None:
# MAGIC                         row.append(None)
# MAGIC                         continue
# MAGIC                     match type_name:
# MAGIC                         case "INT" | "LONG" | "SHORT" | "BYTE":
# MAGIC                             row.append(int(str_value))
# MAGIC                         case "FLOAT" | "DOUBLE" | "DECIMAL":
# MAGIC                             row.append(float(str_value))
# MAGIC                         case "BOOLEAN":
# MAGIC                             row.append(str_value.lower() == "true")
# MAGIC                         case "DATE":
# MAGIC                             row.append(datetime.strptime(str_value, "%Y-%m-%d").date())
# MAGIC                         case "TIMESTAMP":
# MAGIC                             row.append(datetime.strptime(str_value, "%Y-%m-%d %H:%M:%S"))
# MAGIC                         case "BINARY":
# MAGIC                             row.append(bytes(str_value, "utf-8"))
# MAGIC                         case _:
# MAGIC                             row.append(str_value)
# MAGIC                 rows.append(row)
# MAGIC     
# MAGIC             query_result = pd.DataFrame(rows, columns=header)
# MAGIC             return GenieResult(
# MAGIC                 space_id=space_id,
# MAGIC                 conversation_id=conversation_id,
# MAGIC                 question=message,
# MAGIC                 content=content,
# MAGIC                 sql_query=query,
# MAGIC                 sql_query_description=query_description,
# MAGIC                 sql_query_result=query_result,
# MAGIC             )
# MAGIC     
# MAGIC     
# MAGIC     assert databricks_host is not None, "host is not set"
# MAGIC     assert databricks_token is not None, "token is not set"
# MAGIC     assert space_id is not None, "space_id is not set"
# MAGIC     assert question is not None, "question is not set"
# MAGIC     assert contextual_history is not None, "contextual_history is not set"
# MAGIC     client = GenieClient(host=databricks_host, token=databricks_token)
# MAGIC     conversation_id = client.start(space_id)
# MAGIC     formatted_message = f"""Use the contextual history to answer the question. The history may or may not help you. Use it if you find it relevant.
# MAGIC     
# MAGIC     Contextual History: {contextual_history}
# MAGIC     
# MAGIC     Question to answer: {question}
# MAGIC     """
# MAGIC     
# MAGIC     result = client.ask(space_id, conversation_id, formatted_message)
# MAGIC     
# MAGIC     return result.to_string_results()
# MAGIC
# MAGIC $$;
# MAGIC
# MAGIC

# COMMAND ----------

#  _genie_query(
#   secrets("genie_scope", "databricks_host_key"),
#   secrets("genie_scope", "databricks_host_token"),
#   '<space_id>',
#   question, -- retrieved from function
#   contextual_history -- retrieved from function

# COMMAND ----------

# %sql
# CREATE OR REPLACE FUNCTION chat_with_sales_forecast(question STRING COMMENT "the question to ask about billing forecast data",
#                   contextual_history STRING COMMENT "provide relavant history to be able to answer this question, assume genie doesnt keep track of history. Use 'no relevant history' if there is nothing relevant to answer the question.")
# RETURNS STRING
# LANGUAGE SQL
# COMMENT 'This is a agent that you can converse with to get answers to questions about billing and forecasting data about billing. Try to provide simple questions and provide history if you had prior conversations.' 
# RETURN SELECT _genie_query(
#   "https://e2-demo-field-eng.cloud.databricks.com/",
#   secret('dbdemos', 'llm-agent-tools'),
#   '01ef64767add1e6daa55e6299276657d',
#   question, -- retrieved from function
#   contextual_history -- retrieved from function
# );

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION chat_with_sales_forecast(question STRING COMMENT "the question to ask about billing forecast data",
# MAGIC                   contextual_history STRING COMMENT "provide relavant history to be able to answer this question, assume genie doesnt keep track of history. Use 'no relevant history' if there is nothing relevant to answer the question.")
# MAGIC RETURNS STRING
# MAGIC LANGUAGE SQL
# MAGIC COMMENT 'This is a agent that you can converse with to get answers to questions about billing and forecasting data about billing. Try to provide simple questions and provide history if you had prior conversations.' 
# MAGIC RETURN SELECT _genie_query(
# MAGIC   "https://e2-demo-field-eng.cloud.databricks.com/",
# MAGIC   '<token>',
# MAGIC   '01ef64767add1e6daa55e6299276657d', -- genie room
# MAGIC   question, -- retrieved from function
# MAGIC   contextual_history -- retrieved from function
# MAGIC );

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT chat_with_sales_forecast(
# MAGIC   "how many rows of data do you have?",
# MAGIC   "no relevant history"
# MAGIC )

# COMMAND ----------

# MAGIC %md ## Customize

# COMMAND ----------

# MAGIC %md ## Secret

# COMMAND ----------

import time

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

key_name = 'token_01_28_25'

scope_name = 'yevtushenko_tokens'

secret_value = '<token>' #gitleaks:allow

w.secrets.create_scope(scope=scope_name)

w.secrets.put_secret(scope=scope_name, key=key_name, string_value=secret_value)

# cleanup
# w.secrets.delete_secret(scope=scope_name, key=key_name)
# w.secrets.delete_scope(scope=scope_name)

# COMMAND ----------

[i.name for i in w.secrets.list_scopes() if 'yevtushenko' in i.name]

# COMMAND ----------

w.secrets.get_secret(scope='yevtushenko_tokens', key='token_01_28_25').value

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE FUNCTION yevtushenko_artem.dbdemos_agent_tools.get_pat()
# MAGIC   RETURNS STRING
# MAGIC   LANGUAGE PYTHON
# MAGIC   AS $$
# MAGIC     from databricks.sdk import WorkspaceClient
# MAGIC     w = WorkspaceClient()
# MAGIC     pat = w.secrets.get_secret(scope='yevtushenko_tokens', key='token_01_28_25').value
# MAGIC     return pat
# MAGIC   $$

# COMMAND ----------

# MAGIC %sql
# MAGIC select try_secret('yevtushenko_tokens', 'token_01_28_25')

# COMMAND ----------

# MAGIC %sql
# MAGIC select yevtushenko_artem.dbdemos_agent_tools.get_pat()

# COMMAND ----------

# MAGIC %sql
# MAGIC use catalog yevtushenko_artem;
# MAGIC use schema dbdemos_agent_tools;

# COMMAND ----------

workspace = 'https://e2-demo-field-eng.cloud.databricks.com/'
genie_room_id = '01efb3a0a88e15f998c3db34435f2c27'
purpose = 'cambridge mobile telematics api data about trips and crashes'
user_token = '<token>'

# COMMAND ----------

function_definition = f"""
CREATE OR REPLACE FUNCTION chat_with_trip_telemetry_expert(question STRING COMMENT "the question to ask about {purpose}",
        contextual_history STRING COMMENT "provide relavant history to be able to answer this question, assume genie doesnt keep track of history. Use 'no relevant history' if there is nothing relevant to answer the question.")
RETURNS STRING
LANGUAGE SQL
COMMENT 'This is a agent that you can converse with to get answers to questions about {purpose}. Try to provide simple questions and provide history if you had prior conversations.' 
RETURN SELECT _genie_query(
  '{workspace}',
  '{user_token}',
  '{genie_room_id}',
  question,
  contextual_history
);
"""

print(function_definition)

spark.sql(function_definition)

# COMMAND ----------

function_definition = f"""
CREATE OR REPLACE FUNCTION chat_with_trip_telemetry_expert(question STRING COMMENT "the question to ask about {purpose}",
        contextual_history STRING COMMENT "provide relavant history to be able to answer this question, assume genie doesnt keep track of history. Use 'no relevant history' if there is nothing relevant to answer the question.")
RETURNS STRING
LANGUAGE SQL
COMMENT 'This is a agent that you can converse with to get answers to questions about {purpose}. Try to provide simple questions and provide history if you had prior conversations.' 
RETURN SELECT _genie_query(
  '{workspace}',
  try_secret('yevtushenko_tokens', 'token_01_28_25'),
  '{genie_room_id}',
  question,
  contextual_history
);
"""

print(function_definition)

spark.sql(function_definition)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create the Haversine distance function in Unity Catalog
# MAGIC CREATE FUNCTION yevtushenko_artem.dbdemos_agent_tools.haversine_distance_km(
# MAGIC     lat1 DOUBLE,
# MAGIC     lon1 DOUBLE,
# MAGIC     lat2 DOUBLE,
# MAGIC     lon2 DOUBLE
# MAGIC ) RETURNS DOUBLE
# MAGIC LANGUAGE PYTHON
# MAGIC AS $$
# MAGIC import math
# MAGIC
# MAGIC # Radius of the Earth in kilometers
# MAGIC R = 6371.0
# MAGIC
# MAGIC # Convert degrees to radians
# MAGIC lat1_rad = math.radians(lat1)
# MAGIC lon1_rad = math.radians(lon1)
# MAGIC lat2_rad = math.radians(lat2)
# MAGIC lon2_rad = math.radians(lon2)
# MAGIC
# MAGIC # Differences in coordinates
# MAGIC dlat = lat2_rad - lat1_rad
# MAGIC dlon = lat2_rad - lon2_rad
# MAGIC
# MAGIC # Haversine formula
# MAGIC a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
# MAGIC c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
# MAGIC
# MAGIC # Distance in kilometers
# MAGIC distance = R * c
# MAGIC
# MAGIC return distance
# MAGIC $$;
# MAGIC

# COMMAND ----------

# import math

# def haversine_distance(lat1, lon1, lat2, lon2):
#     # Radius of the Earth in kilometers
#     R = 6371.0

#     # Convert degrees to radians
#     lat1_rad = math.radians(lat1)
#     lon1_rad = math.radians(lon1)
#     lat2_rad = math.radians(lat2)
#     lon2_rad = math.radians(lon2)

#     # Differences in coordinates
#     dlat = lat2_rad - lat1_rad
#     dlon = lon2_rad - lon1_rad

#     # Haversine formula
#     a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
#     c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

#     # Distance in kilometers
#     distance = R * c

#     return distance

# # Example usage
# lat1 = 52.2296756
# lon1 = 21.0122287
# lat2 = 41.8919300
# lon2 = 12.5113300

# distance = haversine_distance(lat1, lon1, lat2, lon2)
# print(f"Distance: {distance:.2f} km")