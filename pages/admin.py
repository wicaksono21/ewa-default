import streamlit as st
from firebase_admin import firestore, auth
from datetime import datetime
import pytz
import pandas as pd

class AdminDashboard:
    def __init__(self):
        self.db = firestore.client()
        self.tz = pytz.timezone("Europe/London")
        if 'selected_conversations' not in st.session_state:
            st.session_state.selected_conversations = set()
        if 'show_batch_delete' not in st.session_state:
            st.session_state.show_batch_delete = False
    
    def handle_selection(self, conv_id, is_selected):
        """Handle conversation selection without triggering rerun"""
        if is_selected:
            st.session_state.selected_conversations.add(conv_id)
        else:
            st.session_state.selected_conversations.discard(conv_id)
        st.session_state.show_batch_delete = len(st.session_state.selected_conversations) > 0

    def handle_select_all(self, conversations):
        """Handle select all without triggering rerun"""
        all_ids = {conv.id for conv in conversations}
        if len(st.session_state.selected_conversations) == len(all_ids):
            st.session_state.selected_conversations = set()
        else:
            st.session_state.selected_conversations = all_ids
        st.session_state.show_batch_delete = len(st.session_state.selected_conversations) > 0

    def get_last_login_from_chat(self, user_id):
        """Get user's last login time from their most recent chat message"""
        try:
            # Only get the most recent conversation using orderBy and limit(1)
            latest_conv = self.db.collection('conversations')\
                .where('user_id', '==', user_id)\
                .order_by('updated_at', direction=firestore.Query.DESCENDING)\
                .limit(1)\
                .stream()
        
            # Convert to list to check if any conversations exist
            latest_conv = list(latest_conv)
            if not latest_conv:
                return None

            # Use the updated_at timestamp from the conversation document
            conv_data = latest_conv[0].to_dict()
            return conv_data.get('updated_at')

        except Exception as e:
            st.error(f"Error getting last login: {e}")
            return None

    def create_user_document(self, user):
        """Create or update user document in Firestore"""
        try:
            user_data = {
                'email': user.email,
                'role': 'user',
                'created_at': firestore.SERVER_TIMESTAMP                
            }
            
            self.db.collection('users').document(user.uid).set(user_data)
            return True
        except Exception as e:
            st.error(f"Error creating user document: {e}")
            return False
            
    def sync_users(self):
        """Sync Authentication users with Firestore users collection"""
        try:
            auth_users = auth.list_users().iterate_all()
            synced_count = 0
            
            for auth_user in auth_users:
                user_doc = self.db.collection('users').document(auth_user.uid).get()
                
                if not user_doc.exists:
                    self.create_user_document(auth_user)
                    synced_count += 1
            
            return synced_count
        except Exception as e:
            st.error(f"Error syncing users: {e}")
            return 0
            
    def check_admin_access(self, email):
        """Check if user has admin privileges"""
        try:
            user_ref = self.db.collection('users').where('email', '==', email).limit(1).get()
            if not user_ref:
                return False
            user_data = user_ref[0].to_dict()
            return user_data.get('role') == 'admin'
        except Exception as e:
            st.error(f"Error checking admin access: {e}")
            return False
    
    def delete_conversation(self, conversation_id):
        """Delete a single conversation and all its messages"""
        try:
            messages_ref = self.db.collection('conversations').document(conversation_id).collection('messages')
            self._batch_delete(messages_ref)
            self.db.collection('conversations').document(conversation_id).delete()
            return True
        except Exception as e:
            st.error(f"Error deleting conversation: {e}")
            return False

    def delete_user_conversations(self, user_id):
        """Delete all conversations for a specific user"""
        try:
            conversations = self.db.collection('conversations').where('user_id', '==', user_id).stream()
            for conv in conversations:
                self.delete_conversation(conv.id)
            return True
        except Exception as e:
            st.error(f"Error deleting user conversations: {e}")
            return False

    def delete_multiple_conversations(self, conversation_ids):
        """Delete multiple conversations"""
        try:
            for conv_id in conversation_ids:
                self.delete_conversation(conv_id)
            return True
        except Exception as e:
            st.error(f"Error deleting conversations: {e}")
            return False

    def _batch_delete(self, collection_ref, batch_size=100):
        """Helper method to delete collection in batches"""
        docs = collection_ref.limit(batch_size).stream()
        deleted = 0
        for doc in docs:
            doc.reference.delete()
            deleted += 1
        if deleted >= batch_size:
            return self._batch_delete(collection_ref, batch_size)

    def format_timestamp(self, timestamp):
        """Helper method to format timestamp consistently"""
        if isinstance(timestamp, (datetime, type(firestore.SERVER_TIMESTAMP))):
            try:
                return timestamp.astimezone(self.tz).strftime("%Y-%m-%d %H:%M:%S")
            except AttributeError:
                return 'N/A'
        return 'N/A'
    
    def render_dashboard(self):
        st.title("Admin Dashboard")
        
        # Sync Users Button
        if st.button("Sync Authentication Users", key="sync_users_btn"):
            synced_count = self.sync_users()
            if synced_count > 0:
                st.success(f"Successfully synced {synced_count} new users to Firestore")
            else:
                st.info("All users are already synced")
        
        # Get counts for metrics
        users_count = len(list(self.db.collection('users').get()))
        convs = list(self.db.collection('conversations').get())
        convs_count = len(convs)
        
        # Display metrics
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Users", users_count)
        with col2:
            st.metric("Total Conversations", convs_count)
               
        # User Management
        st.subheader("User Management")
        users_ref = self.db.collection('users').stream()
        users = []

        # Process users with proper error handling
        for doc in users_ref:
            user_data = doc.to_dict()
    
            # Get last login from chat history
            last_login = self.get_last_login_from_chat(doc.id)
            last_login_ts = last_login if last_login else datetime.min.replace(tzinfo=pytz.UTC)
    
            users.append({
                "id": doc.id,
                "email": user_data.get('email', 'N/A'),
                "role": user_data.get('role', 'N/A'),
                "last_login": self.format_timestamp(last_login),
                "last_login_ts": last_login_ts  # Add timestamp for sorting
            })
            
        # Sort users by last login timestamp
        users.sort(key=lambda x: x['last_login_ts'], reverse=True)
        
         # Create user table with processed data
        if users:
            st.table({
                'Email': [user['email'] for user in users],
                'Role': [user['role'] for user in users],
                'Last Active': [user['last_login'] for user in users]
            })
        else:
            st.info("No users found in the database.")
        
        # Essay History
        st.subheader("Essay History")
        selected_email = st.selectbox(
            "Select user to view essay history",
            options=[user.get('email') for user in users],
            index=None,
            placeholder="Choose a user...",
            key="user_select"
        )
        
        if selected_email:
            selected_user = next((user for user in users if user.get('email') == selected_email), None)
            
            if selected_user:
                # Add delete all conversations button with double confirmation
                if st.button("Delete All User Conversations", 
                         key=f"delete_all_{selected_user['id']}",
                         type="primary",
                         use_container_width=True):
                    if st.session_state.get('confirm_delete_all'):
                        if self.delete_user_conversations(selected_user['id']):
                            st.success("All conversations deleted successfully")
                            st.rerun()
                        st.session_state.confirm_delete_all = False
                    else:
                        st.session_state.confirm_delete_all = True
                        st.warning("Are you sure? Click again to confirm deletion of ALL conversations.")

                # Get conversations
                conversations = list(self.db.collection('conversations')
                                  .where('user_id', '==', selected_user['id'])
                                  .order_by('updated_at', direction=firestore.Query.DESCENDING)
                                  .stream())

                # Show batch operations controls in a fixed position
                if st.session_state.show_batch_delete:
                    st.markdown(
                        f"""
                        <div style='position: fixed; top: 0; right: 0; padding: 1rem; 
                        background-color: #262730; z-index: 1000; border-radius: 0.5rem; 
                        margin: 1rem; box-shadow: 0 0 10px rgba(0,0,0,0.5);'>
                            <p style='color: white; margin: 0;'>Selected: {len(st.session_state.selected_conversations)}</p>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                    st.button(
                        f"🗑️ Delete Selected ({len(st.session_state.selected_conversations)})", 
                        type="primary",
                        key="batch_delete",
                        on_click=lambda: self.delete_multiple_conversations(st.session_state.selected_conversations) 
                        and st.rerun()
                    )

                # Select All checkbox
                if conversations:
                    st.checkbox("Select All", 
                              value=len(st.session_state.selected_conversations) == len(conversations),
                              on_change=self.handle_select_all,
                              args=(conversations,),
                              key="select_all")

                # For each conversation
                for conv in conversations:
                    conv_data = conv.to_dict()
                    conv_title = conv_data.get('title', 'Untitled')
                    
                    # Checkbox for batch selection
                    col1, col2 = st.columns([0.1, 0.9])
                    with col1:
                        is_selected = conv.id in st.session_state.selected_conversations
                        if st.checkbox("", key=f"select_{conv.id}", 
                                     value=is_selected,
                                     on_change=self.handle_selection,
                                     args=(conv.id, not is_selected)):
                            pass  # Selection handled in on_change callback
                    
                    with col2:
                        with st.expander(f"View Essay: {conv_title}", expanded=True):
                            messages = self.db.collection('conversations').document(conv.id)\
                                      .collection('messages')\
                                      .order_by('timestamp')\
                                      .stream()
                            
                            detailed_data = []
                            prev_msg_time = None
                            
                            for msg in messages:
                                msg_data = msg.to_dict()
                                timestamp = msg_data.get('timestamp')
                                
                                if timestamp:
                                    date = timestamp.astimezone(self.tz).strftime('%Y-%m-%d')
                                    time = timestamp.astimezone(self.tz).strftime('%H:%M:%S')
                                    
                                    if prev_msg_time:
                                        curr_seconds = int(time.split(':')[0]) * 3600 + \
                                                     int(time.split(':')[1]) * 60 + \
                                                     int(time.split(':')[2])
                                        prev_seconds = int(prev_msg_time.split(':')[0]) * 3600 + \
                                                     int(prev_msg_time.split(':')[1]) * 60 + \
                                                     int(prev_msg_time.split(':')[2])
                                        response_time = curr_seconds - prev_seconds
                                    else:
                                        response_time = 'N/A'
                                        
                                    prev_msg_time = time
                                else:
                                    date = 'N/A'
                                    time = 'N/A'
                                    response_time = 'N/A'
                                    
                                content = msg_data.get('content', '')
                                word_count = len(content.split()) if content else 0
                                
                                detailed_data.append({
                                    'date': date,
                                    'time': time,
                                    'role': msg_data.get('role', 'N/A'),
                                    'content': content,
                                    'length': word_count,
                                    'response_time': response_time
                                })
                            
                            if detailed_data:
                                st.dataframe(
                                    detailed_data,
                                    column_config={
                                        "date": "Date",
                                        "time": "Time",
                                        "role": "Role",
                                        "content": "Content",
                                        "length": st.column_config.NumberColumn(
                                            "Length",
                                            help="Number of words"
                                        ),
                                        "response_time": st.column_config.NumberColumn(
                                            "Response Time (s)",
                                            help="Time since previous message in seconds"
                                        )
                                    },
                                    hide_index=True,
                                    key=f"dataframe_{conv.id}"
                                )
                                
                                # Create columns for buttons at the bottom
                                col1, col2 = st.columns([5,1])
                                with col1:
                                    df = pd.DataFrame(detailed_data)
                                    csv = df.to_csv(index=False).encode('utf-8')
                                    st.download_button(
                                        label="Download Chat Log as CSV",
                                        data=csv,
                                        file_name=f"{conv_title}_chat_log.csv",
                                        mime="text/csv",
                                        key=f"download_{conv.id}"
                                    )
                                with col2:
                                    if st.button("Delete", key=f"delete_{conv.id}", type="primary"):
                                        if self.delete_conversation(conv.id):
                                            st.rerun()
                            else:
                                st.info("No messages found for this essay.")

def main():
    st.set_page_config(
        page_title="Essay Writing Assistant - Admin", 
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    if 'user' not in st.session_state:
        st.error("Please log in first")
        return
        
    admin = AdminDashboard()
    if not admin.check_admin_access(st.session_state.user.email):
        st.error("Access denied. Admin privileges required.")
        return
        
    admin.render_dashboard()

if __name__ == "__main__":
    main()
