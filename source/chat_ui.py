import html,re
from PySide6.QtCore import Qt,QTimer,Signal
from PySide6.QtWidgets import QWidget,QFrame,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QScrollArea,QTextBrowser,QPlainTextEdit

class ChatInput(QPlainTextEdit):
    returnPressed=Signal()
    def __init__(self):
        super().__init__(); self.setMinimumHeight(72); self.setMaximumHeight(110); self.setStyleSheet('background:#141d2c;border:1px solid #3b526a;border-radius:12px;padding:10px;font-size:14px;')
    def text(self): return self.toPlainText()
    def setText(self,text): self.setPlainText(text)
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.returnPressed.emit(); event.accept()
        else: super().keyPressEvent(event)

def rich_text(text):
    result=[]
    for block in text.split('\n\n'):
        lines=block.strip().splitlines()
        if not lines: continue
        for i,line in enumerate(lines):
            escaped=html.escape(line)
            escaped=re.sub(r'\*\*(.+?)\*\*',r'<b>\1</b>',escaped)
            if line.startswith(('• ','- ')): result.append('<p style="margin:5px 0 5px 12px">'+escaped+'</p>')
            elif i==0 and (len(line)<70 and len(lines)>1 or line in ('Когда нельзя','Где есть исключения','Что могут требовать','Условия и исключения')): result.append('<p style="margin:14px 0 7px"><b>'+escaped+'</b></p>')
            else: result.append('<p style="margin:0 0 9px">'+escaped+'</p>')
    return '<div style="font-size:14px;line-height:150%">'+''.join(result)+'</div>'

class ChatView(QScrollArea):
    def __init__(self):
        super().__init__(); self.setWidgetResizable(True); self.setFrameShape(QFrame.NoFrame); self.follow_bottom=True
        self.verticalScrollBar().rangeChanged.connect(lambda low,high:self.verticalScrollBar().setValue(high) if self.follow_bottom else None)
        self.verticalScrollBar().sliderPressed.connect(lambda:setattr(self,'follow_bottom',False))
        self.surface=QWidget(); self.surface.setStyleSheet('background: #0d111b;'); self.setWidget(self.surface)
        self.flow=QVBoxLayout(self.surface); self.flow.setContentsMargins(4,10,8,12); self.flow.setSpacing(18); self.flow.addStretch()
    def clear(self):
        while self.flow.count():
            item=self.flow.takeAt(0)
            if item.widget(): item.widget().hide(); item.widget().deleteLater()
        self.flow.addStretch()
    def set_messages(self,messages,pending=False):
        self.follow_bottom=True
        self.clear()
        if not messages:
            intro=QLabel('<span style="font-size:25px;color:#e9edf5">Что разберём?</span><br><br><span style="color:#8e9cb4">Спроси о законах или опиши ситуацию.<br>Можно продолжать разговор короткими уточнениями.</span>'); intro.setAlignment(Qt.AlignCenter); intro.setMinimumHeight(210); self.flow.insertWidget(0,intro)
        for message in messages: self.add_message(message)
        if pending: self.add_message({'role':'assistant','content':'Смотрю статьи…'})
        self.flow.invalidate(); self.surface.updateGeometry()
        QTimer.singleShot(80,lambda:self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))
    def wheelEvent(self,event):
        self.follow_bottom=False; super().wheelEvent(event)
    def add_message(self,message):
        user=message['role']=='user'; bubble=QFrame(); bubble.setMaximumWidth(640 if user else 780)
        bubble.setStyleSheet('QFrame { background: '+('#203d46' if user else '#161e2e')+';border:1px solid '+('#35636a' if user else '#29334a')+';border-radius:14px;} QLabel {border:0;background:transparent;}')
        layout=QVBoxLayout(bubble); layout.setContentsMargins(20,15,20,16); layout.setSpacing(7)
        who=QLabel('Ты' if user else 'Помощник'); who.setStyleSheet('color:'+('#8fe8d6' if user else '#9caecc')+';font-size:11px;font-weight:600;'); layout.addWidget(who)
        body=QLabel(rich_text(message.get('content',''))); body.setWordWrap(True); body.setTextInteractionFlags(Qt.TextSelectableByMouse|Qt.LinksAccessibleByMouse); layout.addWidget(body)
        sources=message.get('sources',[])
        if sources:
            links=[]; seen=set()
            for source in sources:
                key=(source['url'],source.get('reference',''))
                if key in seen: continue
                seen.add(key)
                if not source['url'].startswith(('https://','http://')): continue
                label=f'[{source.get("number",len(links)+1)}] '+source.get('reference',source['title'])
                links.append('<a style="color:#8fd9ce" href="'+html.escape(source['url'],quote=True)+'">'+html.escape(label)+'</a>')
            citation=QLabel(' &nbsp; · &nbsp; '.join(links)); citation.setWordWrap(True); citation.setOpenExternalLinks(True); citation.setStyleSheet('font-size:11px;'); layout.addWidget(citation)
        if message.get('details'):
            toggle=QPushButton('Статьи и подробности'); toggle.setCheckable(True); toggle.setStyleSheet('text-align:left;background:transparent;color:#95a8c5;border:0;padding:6px 0;')
            detail=QTextBrowser(); detail.setOpenExternalLinks(True); detail.setPlainText(message['details']); detail.setMinimumHeight(250); detail.setMaximumHeight(380); detail.hide()
            def show_details(value,widget=detail): self.follow_bottom=False; widget.setVisible(value)
            toggle.toggled.connect(show_details); layout.addWidget(toggle); layout.addWidget(detail)
        row=QWidget(); row.setStyleSheet('background:transparent;'); line=QHBoxLayout(row); line.setContentsMargins(0,0,0,0)
        if user: line.addStretch(1)
        line.addWidget(bubble,4)
        if not user: line.addStretch(1)
        self.flow.insertWidget(max(0,self.flow.count()-1),row)
