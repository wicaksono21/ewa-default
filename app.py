import streamlit as st
import firebase_admin
from firebase_admin import credentials, auth, firestore
from openai import OpenAI
from datetime import datetime
import pytz
import requests

# Import configurations
from stageprompts import INITIAL_ASSISTANT_MESSAGE
#from reviewinstructions import MODULE_LEARNING_OBJECTIVES, SYSTEM_INSTRUCTIONS
#from readings import READINGS_INDEX, READINGS_BY_TITLE

# Disclaimer 
DISCLAIMER = (
    "### Disclaimer:\n"
    "This feedback is intended to guide your revision and improvement. "
    "It should **not** replace your own self-assessment. "
    "Please read the assignment requirement independently to develop your ability to evaluate your own work critically "
    "and build self-assessment skills."
)

# Initialize Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["FIREBASE"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

#def save_essay_draft(user_id, draft):
 #   db.collection('essays').document(user_id).set({'draft': draft})

# Page setup
st.set_page_config(page_title="Essay Writing Assistant", layout="wide")
st.markdown("""
    <style>
        .main { max-width: 800px; margin: 0 auto; }
        .chat-message { padding: 1rem; margin: 0.5rem 0; border-radius: 0.5rem; }
        #MainMenu, footer { visibility: hidden; }
    </style>
""", unsafe_allow_html=True)

class EWA:
    def __init__(self):        
        self.db = firestore.client()
        self.tz = pytz.timezone("Europe/London")
        self.conversations_per_page = 10  # Number of conversations per page


    def format_time(self, dt=None):
        """Format datetime with consistent timezone"""
        if isinstance(dt, (datetime, type(firestore.SERVER_TIMESTAMP))):
            return dt.strftime("[%Y-%m-%d %H:%M:%S]")
        dt = dt or datetime.now(self.tz)
        return dt.strftime("[%Y-%m-%d %H:%M:%S]")           

    def get_conversations(self, user_id):
        """Retrieve conversation history from Firestore"""
        # Get total conversation count
        count = len(list(db.collection('conversations')
                        .where('user_id', '==', user_id)
                        .stream()))
    
        # Calculate start position based on current page
        page = st.session_state.get('page', 0)
        start = page * 10
    
        return db.collection('conversations')\
                 .where('user_id', '==', user_id)\
                 .order_by('updated_at', direction=firestore.Query.DESCENDING)\
                 .offset(start)\
                 .limit(10)\
                 .stream(), count > (start + 10)

    def render_sidebar(self):
        """Render sidebar with conversation history"""
        with st.sidebar:
            st.title("Essay Writing Assistant")
        
            if st.button("New Session"):
                user = st.session_state.user
                st.session_state.clear()
                st.session_state.user = user
                st.session_state.logged_in = True
                st.session_state.messages = [
                    {**INITIAL_ASSISTANT_MESSAGE, "timestamp": self.format_time()}
                ]
                st.session_state.page = 0
                st.rerun()
            
            if st.button("Latest Chat History"):
                st.session_state.page = 0
                st.rerun()
            
            st.divider()
        
            # Initialize page if not exists
            if 'page' not in st.session_state:
                st.session_state.page = 0
            
            # Get conversations and has_more flag
            convs, has_more = self.get_conversations(st.session_state.user.uid)
        
            # Display conversations
            for conv in convs:
                conv_data = conv.to_dict()
                if st.button(f"{conv_data.get('title', 'Untitled')}", key=conv.id):
                    messages = db.collection('conversations').document(conv.id)\
                               .collection('messages').order_by('timestamp').stream()
                    st.session_state.messages = []
                    for msg in messages:
                        msg_dict = msg.to_dict()
                        if 'timestamp' in msg_dict:
                            msg_dict['timestamp'] = self.format_time(msg_dict['timestamp'])
                        st.session_state.messages.append(msg_dict)
                    st.session_state.current_conversation_id = conv.id
                    st.rerun()
            
            # Simple pagination controls
            cols = st.columns(2)
            with cols[0]:
                if st.session_state.page > 0:
                    if st.button("Previous"):
                        st.session_state.page -= 1
                        st.rerun()
            with cols[1]:
                if has_more:
                    if st.button("Next"):
                        st.session_state.page += 1
                        st.rerun()
    
    def handle_chat(self, prompt):
        """Process chat messages and manage conversation flow"""
        if not prompt:
            return

        current_time = datetime.now(self.tz)
        time_str = self.format_time(current_time)

        # Display user message
        st.chat_message("user").write(f"{time_str} {prompt}")

        # Build messages context
        #messages = [
        #    {"role": "system", "content": SYSTEM_INSTRUCTIONS},
        #    {"role": "system", "content": MODULE_LEARNING_OBJECTIVES},
        #    {"role": "system", "content": READINGS_INDEX}
       #]

               
        # Check for review/scoring related keywords
        review_keywords = ["grade", "score", "review", "assess", "evaluate", "feedback", "rubric"]
        is_review = any(keyword in prompt.lower() for keyword in review_keywords)
    
        if is_review:            
            messages.append({
                "role": "system",
                "content": REVIEW_INSTRUCTIONS            
            })            
            max_tokens = 5000
            context_window = 10  # Larger context window for review tasks         
        else:            
            max_tokens = 400
            context_window = 6   # Smaller context window for regular chat

        # On-demand reading injection based on title keywords
        #prompt_lower = prompt.lower()
        #for title_snippet, reading_text in READINGS_BY_TITLE.items():
        #    if title_snippet in prompt_lower:
        #        messages.append({
        #            "role": "system",
        #            "content": (
        #                f"Relevant course reading for this query "
        #                f"(full text for reference):\n\n{reading_text}"
        #            ),
        #        })
        #        break  # only inject one reading per query

        # Add conversation history (strip invalid keys like timestamp)
        if 'messages' in st.session_state:
            recent_messages = st.session_state.messages[-context_window:]

        if st.session_state.messages and st.session_state.messages[0].get('role') == 'assistant':
            if recent_messages and recent_messages[0].get('role') != 'assistant':
                recent_messages = [st.session_state.messages[0]] + recent_messages[-context_window+1:]

        for m in recent_messages:
            messages.append({"role": m["role"], "content": m["content"]})


        # Add current prompt
        messages.append({"role": "user", "content": prompt})

        try:
            # Prepare user message for history
            user_message = {"role": "user", "content": prompt, "timestamp": time_str}

            # Stream the assistant message
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                full_response = ""
                # Call Responses API with web search (browsing)
                client = OpenAI(api_key=st.secrets["default"]["OPENAI_API_KEY"])

                with client.responses.stream(
                    model="gpt-4.1-mini",
                    #tools=[{"type": "web_search"}],
                    input=messages, #+ [{"role": "system", "content": "Use web search if relevant."}],
                    temperature=0,
                    max_output_tokens=max_tokens,
                ) as stream:
                    for event in stream:
                        if event.type == "response.output_text.delta":
                            full_response += event.delta
                            message_placeholder.markdown(full_response + "▌")

                message_placeholder.markdown(full_response)


            # Handle review/disclaimer logic after streaming
            assistant_content = full_response
            if is_review:
                # Hide Metacognitive Steps and any scores from students
                assistant_content = assistant_content.split("Metacognitive Steps", 1)[-1]
                assistant_content = assistant_content.replace("Estimated Grade:", "").replace("Total Score:", "")
                assistant_content = f"{assistant_content.strip()}\n\n{DISCLAIMER}"

            # Update session state messages
            if 'messages' not in st.session_state:
                st.session_state.messages = []

            assistant_msg = {"role": "assistant", "content": assistant_content, "timestamp": time_str}
            st.session_state.messages.extend([user_message, assistant_msg])

            # Save to database 
            conversation_id = st.session_state.get('current_conversation_id')
            conversation_id = self.save_message(conversation_id, {**user_message, "timestamp": current_time})
            self.save_message(conversation_id, {**assistant_msg, "timestamp": current_time})

        except Exception as e:
            st.error(f"Error processing message: {str(e)}")

    def save_message(self, conversation_id, message):
        """Save message and update title with summary"""
        current_time = datetime.now(self.tz)

        try:
            # For new conversation
            if not conversation_id:
                new_conv_ref = db.collection('conversations').document()
                conversation_id = new_conv_ref.id
                new_conv_ref.set({
                    'user_id': st.session_state.user.uid,
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'updated_at': firestore.SERVER_TIMESTAMP,
                    'title': f"{current_time.strftime('%b %d, %Y')} • New Chat [1📝]",
                    'status': 'active'
                })
                st.session_state.current_conversation_id = conversation_id
        
            # Save message
            conv_ref = db.collection('conversations').document(conversation_id)
            conv_ref.collection('messages').add({
                **message,
                "timestamp": firestore.SERVER_TIMESTAMP
            })

            # Get messages for count and context
            messages = list(conv_ref.collection('messages').get())
            count = len(messages)
        
            # Get last 5 messages for context
            recent_messages = [msg.to_dict()['content'] for msg in messages[-5:]]
            context = " ".join(recent_messages)
        
            # Get summary from GPT
            summary = OpenAI(api_key=st.secrets["default"]["OPENAI_API_KEY"]).chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Create a 2-4 word title for this conversation."},
                    {"role": "user", "content": context}
                ],
                temperature=0.3,
                max_tokens=20
            ).choices[0].message.content.strip()
        
            # Update conversation with summary title and count
            conv_ref.set({
                'updated_at': firestore.SERVER_TIMESTAMP,
                'title': f"{current_time.strftime('%b %d, %Y')} • {summary} [{count}📝]"
            }, merge=True)
        
            return conversation_id
            
        except Exception as e:
            st.error(f"Error: {str(e)}")
            return conversation_id
        
    def login(self, email, password):
        """Authenticate user with Firebase Auth REST API"""
        try:
            # Firebase Auth REST API endpoint
            auth_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={st.secrets['default']['apiKey']}"
        
            # Request body
            auth_data = {
                "email": email,
                "password": password,
                "returnSecureToken": True
            }
        
            # Make authentication request
            response = requests.post(auth_url, json=auth_data)
            if response.status_code != 200:
                raise Exception("Authentication failed")
            
            # Get user details
            user = auth.get_user_by_email(email)
            st.session_state.user = user
            st.session_state.logged_in = True 
            st.session_state.messages = [{
                **INITIAL_ASSISTANT_MESSAGE,
                "timestamp": self.format_time()
            }]
            st.session_state.stage = 'initial'
            #draft_doc = db.collection('essays').document(user.uid).get()
            #if draft_doc.exists:
            #    st.session_state["essay_draft"] = draft_doc.to_dict().get('draft', "")
            #else:
            #    st.session_state["essay_draft"] = ""
            return True
        
        except Exception as e:
            st.error("Login failed")
            return False

    def signup(self, email, password):
        """Register new user with Firebase Auth REST API"""
        try:
            # Firebase Auth REST API endpoint for signup
            signup_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={st.secrets['default']['apiKey']}"
        
            # Request body
            signup_data = {
                "email": email,
                "password": password,
                "returnSecureToken": True
            }
        
            # Make signup request
            response = requests.post(signup_url, json=signup_data)
            if response.status_code != 200:
                error_data = response.json()
                error_message = error_data['error']['message']
                if error_message == "EMAIL_EXISTS":
                    raise Exception("Email already exists")
                raise Exception(error_message)
            
            # Get user details and create user document
            user = auth.get_user_by_email(email)
            self.db.collection('users').document(user.uid).set({
                'email': email,
                'role': 'user',
                'created_at': firestore.SERVER_TIMESTAMP
            })
        
            return True
        
        except Exception as e:
            st.error(f"Signup failed: {str(e)}")
            return False

def main():
    app = EWA()

    # Login page
    if not st.session_state.get('logged_in', False):
        st.title("Essay Writing Assistant")

        tab1, tab2 = st.tabs(["Login", "Sign Up"])
        
        with tab1:
            with st.form("login"):
                email = st.text_input("Email")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Login", use_container_width=True):
                    if app.login(email, password):
                        st.rerun()
        
        with tab2:
            with st.form("signup"):
                new_email = st.text_input("Email")
                new_password = st.text_input("Password", type="password")
                if st.form_submit_button("Sign Up", use_container_width=True):
                    if app.signup(new_email, new_password):
                        st.success("Account created successfully! Please login.")
        return

    # Main chat interface
    st.title("Academic Essay Writing Assistant - TEAP 2025/2026")
    app.render_sidebar()

     # Display message history
    if 'messages' in st.session_state:
        for msg in st.session_state.messages:
            st.chat_message(msg["role"]).write(
                f"{msg.get('timestamp', '')} {msg['content']}"
            )

    # Chat input
    if prompt := st.chat_input("Type your message here..."):
        app.handle_chat(prompt)

if __name__ == "__main__":
    main()
