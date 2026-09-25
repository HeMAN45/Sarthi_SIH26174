import win32com.client as w, os, sys

src = r"C:\SISH_F\Clippy\ORBITAL-HAR_SIH26174_Hashira_PREMIUM.pptx"
pdf = r"C:\SISH_F\Clippy\..\ORBITAL-HAR_SIH26174_Hashira_PREMIUM.pdf"
app = w.Dispatch("PowerPoint.Application")
pres = app.Presentations.Open(src, WithWindow=False)
pres.SaveAs(pdf, 32)  # 32 = ppSaveAsPDF
pres.Close()
app.Quit()
print("PDF_OK", os.path.exists(pdf))
