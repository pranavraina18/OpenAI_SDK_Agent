import asyncio
import os
import warnings
import logging
from logging.handlers import RotatingFileHandler
from cv2 import log
from dotenv import load_dotenv
from datetime import datetime, date
import mysql.connector
from mysql.connector import pooling
import pandas as pd
from agents import Agent, Runner, function_tool, TResponseInputItem, trace
from fastapi import FastAPI
from pydantic import BaseModel
import shutil
import traceback
from contextlib import asynccontextmanager

# --- Setup ---
load_dotenv()

# Environment Variables
AOL_HOST = os.getenv('AOL_HOST', 'localhost')
AOL_USER = os.getenv('AOL_USER', 'root')
AOL_PASSWORD = os.getenv('AOL_PASSWORD', '')
AOL_DATABASE = os.getenv('aol_database')
AOL_TABLE = os.getenv('aol_table')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Warnings
warnings.filterwarnings("ignore", message=".*pandas only supports SQLAlchemy connectable.*")

# Get the script's directory
script_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(script_dir, f"app_{datetime.now().date()}.log")

# Configure logging
logging.basicConfig(
    level=logging.ERROR,  # Change to INFO if needed
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3),  # Buffered logging
        logging.StreamHandler()  # Console output
    ]
)

# Module-level variable to track last cleanup
last_cleanup_date = None

# --- Global Cache for Summarization ---
_summary_cache = {}

# --- Memory store for ongoing conversations ---
conversation_memory = {}  # {conversation_id: {"input_items": [...], "context": "..." }}

# --- Global Database Pool ---
dbconfig = {
    "host": AOL_HOST,
    "user": AOL_USER,
    "password": AOL_PASSWORD,
    "database": AOL_DATABASE,
    "pool_reset_session": True,
    "connection_timeout": 60,
}
try:
    pool = pooling.MySQLConnectionPool(pool_name="mypool", pool_size=10, **dbconfig)
    logging.info("MySQL connection pool created successfully.")
except Exception as e:
    logging.error(f"Error creating MySQL connection pool: {e}")
    pool = None
    
def connect_to_mysql() -> mysql.connector.MySQLConnection | None:
    try:
        if pool:
            connection = pool.get_connection()
            logging.info("Successfully obtained a connection from the pool.")
            return connection
        else:
            logging.warning("Connection pool is not available.")
            return None
    except Exception as e:
        logging.error(f"Error connecting to MySQL: {e}")
        return None
    
# --- Database Helpers ---
def get_table_columns(table_name: str) -> list[str]:
    """Get all columns from a table."""
    try:
        conn = connect_to_mysql()
        cursor = conn.cursor()
        cursor.execute(f"DESCRIBE {table_name}")
        columns = [col[0] for col in cursor.fetchall()]
        cursor.close()
        conn.close()
        return columns
    except Exception as e:
        logging.error(f"Error getting table columns: {e}")
        return []

def get_table_row_count(table_name: str) -> int:
    """Get row count for a table."""
    try:
        conn = connect_to_mysql()
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        (count,) = cursor.fetchone()
        cursor.close()
        conn.close()
        return count
    except Exception as e:
        logging.error(f"Error getting row count: {e}")
        return 0

# --- Tools ---
@function_tool
def generate_sql_query(user_input: str, columns: list[str], table_name: str) -> str:
    prompt = (
        f"You are a smart SQL assistant specialized in generating MySQL queries only.\n"
        f"You understand natural language and can infer the user's intent.\n"
        f"Given the table '{table_name}' with the columns: {', '.join(columns)}\n"
        f"User request: '{user_input}'\n\n"
        f"Instructions:\n"
        f"- Always generate SQL that is 100% compatible with MySQL syntax. Do not use any PostgreSQL, SQL Server, or non-MySQL functions.\n"
        f"- If the user asks for a time period (e.g., this year, last month, this quarter), use MySQL functions like YEAR(), MONTH(), QUARTER(), CURDATE(), etc.\n"
        f"- Interpret conditions such as price ranges, availability, and other column-based filters.\n"
        f"- Recognize implied conditions, such as ordering results or applying limits.\n"
        f"- Exclude any records where 'worker_name' LIKE '%test%' to avoid test data.\n"
        f"- Exclude columns and values that may contain sensitive information. Never include the following fields in SELECT or WHERE clauses: email, phone, phone_number, contact, address, home_address, personal_email, etc.\n"
        f"- If a sensitive column is explicitly requested, return an error message asking the user to rephrase their request without sensitive data.\n"
        f"- Default to sorting by the most recent date if no sorting is specified (use ORDER BY with a date field if available).\n"
        f"- Never use DATE_TRUNC, INTERVAL 'x' MONTH, or any non-MySQL functions.\n"
        f"- If needed, use MySQL syntax like DATE_ADD(), DATE_SUB(), and STR_TO_DATE().\n\n"
        f"Now, generate a smart MySQL query based on the user's request."
    )
    return prompt


@function_tool
async def execute_sql_query(query: str) -> list[dict]:
    try:
        with connect_to_mysql() as conn:
            df = pd.read_sql_query(query, conn)
            
        if df.empty:
            logging.info(f"No results found for query: {query}")
            return [{"message": "No results found."}]
        return df.to_dict(orient="records")
    except Exception as e:
        logging.error(f"SQL Execution error: {e}")
        return [{"error": str(e)}]

@function_tool
async def summarize_table_distribution(table_name: str, sample_percent: int) -> dict:
    try:
        sample_percent = sample_percent or 10
        cache_key = f"{table_name}_{sample_percent}"
        if cache_key in _summary_cache:
            logging.info(f"Using cached summary for {cache_key}")
            return _summary_cache[cache_key]

        total_rows = get_table_row_count(table_name)
        sample_size = max(1, int(total_rows * sample_percent / 100))

        with connect_to_mysql() as conn:
            df = pd.read_sql(f"SELECT * FROM {table_name} ORDER BY RAND() LIMIT {sample_size}", conn)

        if df.empty:
            return {"message": "No data found."}

        summary = {
            "sample_size": len(df),
            "total_rows": total_rows,
            "numeric_summary": df.select_dtypes(include="number").describe().to_dict(),
            "top_categorical_values": {
                col: df[col].value_counts().head(10).to_dict()
                for col in df.select_dtypes(include="object")
            },
        }
        _summary_cache[cache_key] = summary
        return summary

    except Exception as e:
        logging.error(f"Summarization error: {e}")
        return {"error": str(e)}

@function_tool
def execute_chart_code(python_code: str) -> str:
    """
    Executes the AI-generated matplotlib code to create a chart.
    Returns the path to the saved chart file.
    """
    # Prepare a local dictionary for execution
    local_vars ={}
    
    try:
        local_vars['__file__'] = __file__
    except NameError:
        local_vars['__file__'] = os.getcwd()  # Fallback to current working directory
            
    try:
        # Execute the AI-generated code inside a safe local environment
        exec(python_code, {}, local_vars)        
       
        # Retrieve the chart metadata
        file_path = local_vars.get("filepath")
        if file_path:
            return file_path
        else:
            logging.error("Chart metadata not found after execution.")
            return "Error: Chart metadata not found after execution."
    
    except Exception as e:
        tb = traceback.format_exc()
        logging.error(f"Exception during code execution:\n{tb}")
        return f"Runtime Error: {e}. Full Traceback:\n{tb}"
    except SyntaxError as se:
        logging.error(f"Syntax error during code execution: {se}")
        return f"Syntax Error: {se}. Check for unclosed strings, mismatched quotes, or invalid syntax."
    
@function_tool
async def generate_chart_with_agent(message: str) -> dict:
    """
    Generate Python code for a chart based on the provided user message (data/analysis)
    and return the Python code as a string, with dynamic chart type, file paths, and metadata.
    """
    try:
        # Ensure 'charts/' directory exists
        charts_dir = os.path.join(script_dir, "charts")
        os.makedirs(charts_dir, exist_ok=True)

        # Setup agent
        visualize_agent = Agent(
            name="VisualizeAgent",
            instructions = """
                You are a Python visualization code generator.
                Your task is to generate clean, executable Python code that creates a chart using matplotlib, based on a user's message that contains data and insights.

                RULES TO FOLLOW STRICTLY:
                - DO NOT wrap your Python code in triple backticks (no ```python or ```).
                - The output MUST be valid, runnable Python code only — no Markdown, no commentary, just code.
                - Ensure all string literals are properly closed with matching quotes. Avoid unterminated strings.
                - Escape all special characters inside string literals (e.g., use \\n, \\", \\' where necessary).
                - Escape quotes inside strings: use \\" for double quotes and \\' for single quotes inside string literals.
                - DO NOT use multiline triple-quoted strings unless absolutely necessary.
                - DO NOT use plt.show() — NEVER display the chart. Only save it directly to a file.

                DATA HANDLING:
                - Extract x (e.g., categories or labels) and y (e.g., numerical values) data from the message.
                - Ensure the lengths of the x and y data lists match. If not, truncate the longer list to the length of the shorter one.
                - Clean the data: remove any NaN or missing values from both x and y lists.
                - If there's insufficient data (e.g., fewer than 2 points), raise a ValueError with a helpful message.

                CHART TYPE SELECTION:
                The chart type is selected dynamically based on the data:
                - Bar Chart: For comparing categories, where x is categorical and y is numerical.
                - Line Chart: For showing trends over time, where x is time and y is numerical.
                - Pie Chart: For showing proportions, when the data is categorical and represents parts of a whole.
                - Scatter Plot: For displaying numeric correlations, where both x and y are numeric.
                - Histogram: For showing the distribution of a single numerical variable.
                - Box Plot: For showing the spread and outliers of a numerical variable.

                PLOTTING:
                - Create the appropriate matplotlib chart based on the selected chart type.
                - Add a descriptive title, X and Y axis labels, and rotate tick labels if necessary.
                - Use a clean, readable color palette:
                    - For bar charts, use a gradient colormap such as `plt.cm.viridis` or `plt.cm.plasma`.
                    - Example: `colors = plt.cm.viridis(np.linspace(0, 1, len(data)))`
                - Save the chart as a PNG file inside a directory named 'charts'.
                - Use the following to get the base path for saving:
                    script_dir = os.path.dirname(os.path.abspath(__file__))
                - Ensure the 'charts' directory exists: os.makedirs(charts_dir, exist_ok=True)
                - DO NOT display the chart with plt.show() — only save it.

                METADATA (REQUIRED):
                At the end of your code, return a dictionary named `chart_metadata` with the following exact keys and variable names:
                - `filepath` (no underscore): the absolute path to the saved PNG file.
                - `chart_type`: a string (e.g., "Bar Chart").
                - `x_column`: a string describing what the x-axis represents.
                - `y_columns`: a list of strings for what the y-axis represents.

                These keys must always use the exact variable names shown above — do not change their spelling, casing, or add underscores.

                Return only this dictionary as the final output — do not wrap it in quotes or add anything else.
                """,
            model="gpt-4.1-mini",
        )

        # Prepare the input
        input_items: list[TResponseInputItem] = [{"content": message, "role": "user"}]

        # Run the agent and get the result
        result = await Runner.run(visualize_agent, input_items)

        if result.final_output:
            if isinstance(result.final_output, bytes):
                logging.error("Unexpected binary output received.")
                return {"error": "Unexpected binary output received."}
            else:
                # Return the Python code directly
                return {"python_code": result.final_output}
        else:
            # Handle the case where no code was generated, returning a custom error message
            logging.error("No code generated in the result.")
            return {"error": "No chart generation code was produced. Please try again."}

    except Exception as e:
        logging.error(f"Chart code generation failed: {e}")
        return {
            "error": f"Chart code generation failed due to an error: {e}. Please try again."
        }
       
class AnalysisRequest(BaseModel):
    user_request: str
    database: str | None = None  
    table_name: str | None = None  
    conversation_id: str | None = None
    
async def async_get_table_columns(table_name: str) -> list[str]:
    return await asyncio.to_thread(get_table_columns, table_name)    

async def compress_context_with_agent(context: list[str]) -> str:
    combined_context = "\n".join(context)
    if len(combined_context) < 8000:
        return combined_context

    summarization_prompt = f"""
            Summarize the following conversation preserving important user questions, answers, and analysis. 
            Skip repetitions or confirmations. Keep the output concise and informative.
            \n\n{combined_context}
            """

    input_items: list[TResponseInputItem] = [{
        "content": summarization_prompt,
        "role": "user"
    }]
    summary_agent = Agent(
        name="SummaryAgent",
        instructions="Summarize conversation context cleanly and quickly.",
        model="gpt-4o",
    )
    try:
        result = await Runner.run(summary_agent, input_items)
        return result.final_output
    except Exception as e:
        logging.error(f"Context compression failed: {e}")
        return combined_context

    
async def run_analysis_agent(user_request: str, database: str, conversation_id:str):
    if "aol" in database.lower():
        database = AOL_DATABASE
        table_name = AOL_TABLE

    columns = await async_get_table_columns(table_name)

    if not columns:
        logging.error(f"Could not retrieve columns for table '{table_name}'.")
        return {"error": f"Could not retrieve columns for table '{table_name}'."}
    
    # --- Load previous memory ---
    memory = conversation_memory.get(conversation_id, {"input_items": [], "context": ""})
    input_items = memory["input_items"]
    context = memory["context"]

    if not input_items:
        # First message: initialize input_items with table description
        input_items.append({
        "content": f"The table '{table_name}' has columns: {', '.join(columns)}.",
        "role": "user"
    })

    agent = Agent(
        name="AnalysisAgent",
        instructions = """
            You are a smart data analysis assistant, designed to deliver insightful summaries, structured data insights, and visualizations when appropriate, ensuring clarity, value, and a seamless user experience.

            ### Workflow:

            1. **Understand the User Request:**
                - **Identify the type of request** (e.g., total, breakdown, comparison, trend, or distribution) to determine the appropriate data analysis and visualization techniques.
                - **Pinpoint key variables** within the request (e.g., campus, month, program) to guide the analysis and generate meaningful insights.
                - **Clarify context** by identifying whether the user is asking for a single summary or a deeper exploration of specific data points.

            2. **Query and Execution:**
                - **Generate an SQL query** based on the user request using `generate_sql_query`. The SQL query should dynamically adapt to the available columns in the database and avoid assumptions about column values.
                - **Check column availability**: Before generating the query, verify if the required columns exist in the table (e.g., check if `runing_status` exists). If the column is not present or the values do not match the user's assumptions, generate an error or prompt for clarification.
                - **Validate values**: Ensure that any values referenced in the query (e.g., 'active') are valid based on the data in the columns. If they are not, either prompt the user to clarify or dynamically adjust based on available data.
                - **Execute the SQL** immediately via `execute_sql_query` to retrieve the relevant data from the database.

            3. **Data Analysis:**
                - **Analyze the query result:**
                    - For **totals**, provide a clear number or summary.
                    - For **breakdowns or comparisons**, structure the data in a readable format (e.g., dictionaries or categorized lists).
                    - **Highlight key patterns, outliers, or trends** that emerge from the data (e.g., significant differences between categories, shifts in values, anomalies).
                - **Prioritize actionable insights** over unnecessary details, always focusing on what’s most valuable to the user.

            4. **Summary (Always Required):**
                - Provide a **concise and professional summary** of the result, ensuring it’s easy to understand and actionable.
                - **Avoid listing raw numbers** unless essential to clarify the key insights.
                - **Do not recreate tables or extensive lists** of data within the text unless absolutely necessary for clarity.
                - **Maintain a neutral, factual tone** throughout, and ensure the summary is tailored to the user’s needs.

           5. **Visualization (Always Recommended for Breakdown/Comparison/Trends):**
                - **Create a visualization** **only if** it adds meaningful context to the data:
                    - **Trends** → use a **line/area chart** to show changes over time.
                    - **Comparisons** → use a **bar/column chart** to compare values across categories.
                    - **Distributions** → use a **pie chart** for proportions or a **histogram** for frequency distributions.
                - **Charts should not be rigidly tied to one type**; instead, select the chart type based on the nature of the data. For example, use bar charts for category comparisons, line charts for time-based trends, or histograms for distribution breakdowns. Always prioritize visual clarity and relevance to the analysis.
                - **Always ensure visual clarity** by incorporating clear **titles, axis labels, legends, and color coding** when appropriate.
                **- Ensure the x and y data arrays used for plotting are the same length. If not, truncate the longer one to match the shorter to avoid runtime errors.**
                - Use `generate_chart_with_agent` to create the chart code and `execute_chart_code` to generate the chart.
                - If a chart is created:
                    - Include a brief note such as "**A chart has been created for your reference: [absolute chart file path]**" — **make sure this is the absolute path**, not just the filename.
                - **Visualization Guidelines**:
                    - **Bar/Column Charts**: Best for comparing values between distinct categories (e.g., student population by campus).
                    - **Pie Charts**: Effective for showing percentage-based data or proportions (e.g., program distribution by campus).
                    - **Line/Area Charts**: Ideal for visualizing trends over time (e.g., enrollment trends over several months).
                    - **Histograms**: Suitable for showing distribution of continuous data (e.g., grade distributions).
                    - **Heatmaps** or **Scatter Plots**: Recommended for multi-dimensional or complex data visualizations (e.g., correlation between multiple variables).

            6. **Follow-up Suggestions (Optional, Only If Helpful):**
                - If relevant, suggest **deeper insights or next steps**:
                    - For example, "Would you like to explore trends over time?" or "Should I compare this data across additional campuses or programs?"
                - **Avoid overloading the user** with suggestions, and only propose follow-ups when they meaningfully add value or enhance the analysis.

            ### Important Rules:
            - **Never expose** SQL queries, table names, or internal processes to the user.
            - **Never output raw code** (e.g., chart generation code) or technical details unless specifically requested.
            - **Avoid repeating** the same data in both the summary and the chart unless absolutely necessary.
            - Keep the summary **clear**, **action-driven**, and **tailored to the user’s needs**, always focusing on what’s important to them.
            - Prioritize **clarity, brevity, and relevance** in all outputs to maintain user engagement and comprehension.
            

            ### Execution Flow (Strict):
            1. **generate_sql_query** → 2. **execute_sql_query** → 3. **analyze and summarize** → (if valuable) 4. **generate_chart_with_agent** → 5. **execute_chart_code** → (optionally) 6. **suggest next steps**
        """,
        tools=[generate_sql_query, execute_sql_query, summarize_table_distribution, generate_chart_with_agent, execute_chart_code],
        model="gpt-4.1-mini",
    )

    with trace("AnalysisAgent", group_id=conversation_id):
        input_items.append({"content": user_request, "role": "user"})
        context = await compress_context_with_agent(context)
        result = await Runner.run(agent, input_items, context=context)
    
    # --- Save updated memory ---
    conversation_memory[conversation_id] = {
        "input_items": result.to_input_list(),
        "context": context,
        "last_active": datetime.now()
    }
        
    if result.final_output:
        return result.final_output
    else:
        logging.error(f"Error: No output generated by the analysis agent.")
        return {"error": "An error occurred during analysis. Please try again later."}

def cleanup_conversations():
    """Clean up conversation memory and the charts folder once per day based on inactivity."""
    global last_cleanup_date
    current_date = date.today()

    if last_cleanup_date == current_date:
        return  # Already cleaned today

    last_cleanup_date = current_date

    # Clean up old conversations
    to_remove = [
        conv_id for conv_id, data in conversation_memory.items()
        if data["last_active"].date() < current_date
    ]
    
    for conv_id in to_remove:
        del conversation_memory[conv_id]
        logging.info(f"Cleaned up conversation memory for conversation {conv_id}")

    # Delete charts folder once per day
    charts_dir = os.path.join(script_dir, "charts")
    if os.path.exists(charts_dir):
        try:
            shutil.rmtree(charts_dir)
            logging.info(f"Deleted charts folder at {charts_dir}")
        except Exception as e:
            logging.error(f"Error deleting charts folder: {e}")

 # Periodic cleanup (every minute)
async def periodic_cleanup():
    """Run the cleanup function periodically in a separate thread."""
    while True:
        cleanup_conversations()
        await asyncio.sleep(60)  # Run every 60 seconds
        
 # Start the cleanup task in FastAPI's startup event or background tasks
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run background task at app startup and shutdown."""
    # Start the cleanup task in the background
    asyncio.create_task(periodic_cleanup())
    yield  # Keep the app running
    logging.info("Shutting down...")
    
# --- FastAPI Integration ---
# FastAPI app initialization with the lifespan context manager
app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
   
@app.post("/analyze/")
async def analyze(request: AnalysisRequest):
    conversation_id = request.conversation_id 
    try:
        result = await run_analysis_agent(request.user_request, request.database, conversation_id)
        if "error" in result:
            return {"error": result["error"]}
        return result
    except Exception as e:
        logging.error(f"API error: {e}")
        return {"error": "Internal server error, please try again later."}

if __name__ == "__main__":
    import uvicorn    
    uvicorn.run(app, host="0.0.0.0", port=8000)
