import asyncio
import os

import psycopg2
from autogen_agentchat.agents import AssistantAgent, CodeExecutorAgent
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_ext.code_executors.local import LocalCommandLineCodeExecutor
from autogen_ext.models.openai import OpenAIChatCompletionClient
from dotenv import load_dotenv
from neon_api import NeonAPI
from psycopg2.extras import RealDictCursor

load_dotenv()

neon_client = NeonAPI(
    api_key=os.environ["NEON_API_KEY"],
)


def create_database(project_name: str) -> str:
    """
    Creates a new Neon project. (this takes less than 500ms)
    Args:
        project_name: Name of the project to create
    Returns:
        the connection URI for the new project
    """
    try:
        project = neon_client.project_create(project={"name": project_name}).project
        connection_uri = neon_client.connection_uri(
            project_id=project.id, database_name="neondb", role_name="neondb_owner"
        ).uri

        return f"Project/database created, connection URI: {connection_uri}"
    except Exception as e:
        return f"Failed to create project: {str(e)}"


def run_sql_query(connection_uri: str, query: str) -> str:
    """
    Runs an SQL query in the Neon database.
    Args:
        connection_uri: The connection URI for the Neon database
        query: The SQL query to execute
    Returns:
        the result of the SQL query
    """
    conn = psycopg2.connect(connection_uri)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(query)
        conn.commit()

        # Try to fetch results (for SELECT queries)
        try:
            records = cur.fetchall()
            return f"Query result: {records}"
        except psycopg2.ProgrammingError:
            # For INSERT/UPDATE/DELETE operations
            return f"Query executed successfully"
    except Exception as e:
        conn.rollback()
        return f"Failed to execute SQL query: {str(e)}"
    finally:
        cur.close()
        conn.close()


async def main() -> None:
    model_client = OpenAIChatCompletionClient(model="gpt-4o", temperature=0.6)

    assistant = AssistantAgent(
        name="assistant",
        system_message="""You are a helpful AI assistant.
Solve tasks using your coding and language skills.
You are working with two other agents:
1. 'code_executor': Use this agent for non-database coding tasks such as general-purpose scripts, file manipulation, and system commands.
2. 'db_admin': Use this agent for all database-related tasks.
Do NOT generate or suggest any SQL or database connection code yourself. Clearly mention what needs to be done and send the request to 'db_admin'.

In the following cases, suggest python code (in a python coding block) or shell script (in a sh coding block) for the user to execute.
1. When you need to collect info, use the code to output the info you need, for example, browse or search the web, download/read a file, print the content of a webpage or a file, get the current date/time, check the operating system. After sufficient info is printed and the task is ready to be solved based on your language skill, you can solve the task by yourself.
2. When you need to perform some task with code, use the code to perform the task and output the result. Finish the task smartly.

Solve the task step by step if you need to. If a plan is not provided, explain your plan first. Be clear which step uses code, and which step uses your language skill.
When using code, you must indicate the script type in the code block. The user cannot provide any other feedback or perform any other action beyond executing the code you suggest. The user can't modify your code. So do not suggest incomplete code which requires users to modify. Don't use a code block if it's not intended to be executed by the user.
If you want the user to save the code in a file before executing it, put # filename: <filename> inside the code block as the first line. Don't include multiple code blocks in one response. Do not ask users to copy and paste the result. Instead, use 'print' function for the output when relevant, try to add print statements while sharing code with the user so it will be used for debugging. Check the execution result returned by the user.
If the result indicates there is an error, fix the error and output the code again. Suggest the full code instead of partial code or code changes. If the error can't be fixed or if the task is not solved even after the code is executed successfully, analyze the problem, revisit your assumption, collect additional info you need, and think of a different approach to try.
When you find an answer, verify the answer carefully. Include verifiable evidence in your response if possible.
Reply 'TERMINATE' in the end when the task is completed by everyone.
""",
        model_client=model_client,
    )

    code_executor = CodeExecutorAgent(
        name="code_executor",
        code_executor=LocalCommandLineCodeExecutor(work_dir="coding"),
        sources=["assistant"],
    )

    db_admin = AssistantAgent(
        name="db_admin",
        system_message="""You are a helpful database admin assistant with access to the following tools:
1.  **Project Creation:** Create a new Neon project by providing a project name and receive the connection URI.
2.  **SQL Execution:** Run SQL queries within a Neon database.
Use these tools to fulfill user requests.  For each step, clearly describe the action taken and its result.  Include the tool output directly in the chat.  When multiple SQL queries are required, combine them into a single grouped query.  Present the output of each individual query within the grouped query's response.
""",
        model_client=model_client,
        tools=[create_database, run_sql_query],
    )

    # The termination condition is a combination of text termination and max message termination, either of which will cause the chat to terminate.
    termination = TextMentionTermination("TERMINATE") | MaxMessageTermination(20)

    # The group chat will alternate between the assistant and the code executor.
    group_chat = RoundRobinGroupChat(
        [assistant, code_executor, db_admin], termination_condition=termination
    )

    # `run_stream` returns an async generator to stream the intermediate messages.
    stream = group_chat.run_stream(
        task="Get the 10 most recent Machine Learning papers from arXiv. Print the titles and links to the papers in the chat. Save them in a database named 'arxiv_papers'",
    )
    await Console(stream)


if __name__ == "__main__":
    asyncio.run(main())
