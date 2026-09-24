import win32com.client as w, os
src=r"C:\SISH_F\Clippy\ORBITAL-HAR_SIH26174_Hashira_PREMIUM.pptx"
app=w.Dispatch("PowerPoint.Application")
pres=app.Presentations.Open(src, WithWindow=False)
for i,sl in enumerate(pres.Slides,1):
    sl.Export(rf"C:\SISH_F\Clippy\_ppt_build\png\s{i}.png","PNG",1600,900)
pres.Close(); app.Quit()
print("done")
