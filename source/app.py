import ctypes
from ctypes import wintypes
import csv
import datetime as dt
import html
import json
import os
from pathlib import Path
import sys
import threading
import traceback

from PySide6.QtCore import Qt,QTimer,QThread,Signal,QAbstractNativeEventFilter,QPoint,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QFont,QIcon,QPixmap,QKeySequence,QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,QGridLayout,QComboBox,QStackedWidget,QTextEdit,QLineEdit,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QMessageBox,QCheckBox,QSlider,QSizeGrip,QSystemTrayIcon,QMenu,QKeySequenceEdit,QScrollArea,QInputDialog,QSizePolicy,QTabWidget)
from chat_ui import ChatView,ChatInput

from core import Store,SERVERS,money
from services import extract_document,sync_forum,local_answer,read_saved_document

DATA=Path(os.environ['NOVA_DATA_DIR']) if 'NOVA_DATA_DIR' in os.environ else Path(os.getenv('LOCALAPPDATA',str(Path.home()))) / 'NovaCompanion'
DEFAULT_KEYS={'main':'Ctrl+Shift+F6','finance':'Ctrl+Shift+F7','notes':'Ctrl+Shift+F8','laws':'Ctrl+Shift+F9','edit':'Ctrl+Shift+F10','prices':'Ctrl+Shift+F11'}
STYLE='''
* { font-family: "Segoe UI"; font-size: 13px; color: #e9edf5; }
QMainWindow, QWidget#root { background: #0d111b; }
QFrame#sidebar { background: #111725; border-right: 1px solid #242d40; }
QFrame#card { background: #161e2e; border: 1px solid #29334a; border-radius: 14px; }
QLabel { background: transparent; }
QLabel#muted { color: #8e9cb4; }
QLabel#eyebrow { color: #6cdbc5; font-size: 11px; font-weight: 700; }
QLabel#title { font-size: 29px; font-weight: 700; }
QLabel#value { font-size: 27px; font-weight: 650; }
QPushButton { background: #232e43; border: 1px solid #33415c; border-radius: 8px; padding: 10px 15px; }
QPushButton:hover { background: #303f5a; border-color: #7798c9; }
QPushButton:checked, QPushButton#primary { background: #6cdbc5; color: #0b2426; font-weight: 650; border-color: #6cdbc5; }
QPushButton#nav { text-align: left; padding: 13px 16px; background: transparent; border: none; color: #9baac2; }
QPushButton#nav:checked { color: #7ce7d0; background: #1d3440; }
QLineEdit,QTextEdit,QComboBox,QKeySequenceEdit { background: #101725; border: 1px solid #33415c; border-radius: 8px; padding: 10px; selection-background-color: #366d6a; }
QTextEdit { line-height: 1.5; }
QComboBox::drop-down { border: none; width: 25px; }
QComboBox QAbstractItemView { background: #172033; selection-background-color: #30465d; }
QTableWidget { background: #121a29; alternate-background-color: #182233; border: 1px solid #29334a; border-radius: 9px; gridline-color: #26324a; }
QHeaderView::section { background: #1b2639; color: #96a9c4; border: none; padding: 10px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #111725; width: 9px; } QScrollBar::handle:vertical { background: #3b4960; border-radius: 4px; min-height: 25px; }
QToolTip { background: #26364e; color: white; padding: 6px; border: 1px solid #52657f; }
QMenu { background: #172033; padding: 7px; } QMenu::item:selected { background: #30465d; }
'''

def label(text='',name=None):
    w=QLabel(text)
    if name: w.setObjectName(name)
    return w

def button(text,fn=None,primary=False):
    b=QPushButton(text)
    if fn: b.clicked.connect(fn)
    if primary: b.setObjectName('primary')
    return b

def card():
    c=QFrame(); c.setObjectName('card'); l=QVBoxLayout(c); l.setContentsMargins(22,18,22,18); l.setSpacing(12)
    return c,l

class Job(QThread):
    done=Signal(object); failed=Signal(str); progress=Signal(str)
    def __init__(self,fn): super().__init__(); self.fn=fn
    def run(self):
        try: self.done.emit(self.fn(self.progress.emit))
        except Exception as e: self.failed.emit(str(e))

class Scanner(QThread):
    result=Signal(object); status=Signal(str)
    def __init__(self): super().__init__(); self.stopping=False; self.enabled=True
    def run(self):
        from vision import capture_game,read_image
        while not self.stopping:
            if self.enabled:
                try:
                    image=capture_game()
                    if image is None: self.status.emit('Ожидание активного окна игры')
                    else:
                        pair,_=read_image(image)
                        if pair is None: self.status.emit('Деньги скрыты · жду игровой интерфейс')
                        else: self.result.emit(pair)
                except Exception as e: self.status.emit('Распознавание: '+str(e)[:160])
            for _ in range(20):
                if self.stopping: return
                self.msleep(100)

class Hotkeys(QAbstractNativeEventFilter):
    triggered=Signal(str)
    # QAbstractNativeEventFilter isn't a QObject; signals are handled through a callback.
    def __init__(self,callback): super().__init__(); self.callback=callback; self.ids={}; self.errors=[]
    def register(self,keys):
        self.clear(); self.errors=[]
        for i,(name,sequence) in enumerate(keys.items(),1):
            seq=QKeySequence(sequence)
            if seq.isEmpty(): self.errors.append(name); continue
            comb=seq[0]; key=comb.key(); mods=comb.keyboardModifiers()
            native=0x4000
            for q,n in [(Qt.ControlModifier,2),(Qt.AltModifier,1),(Qt.ShiftModifier,4)]:
                if mods & q: native|=n
            vk=0x70+int(key)-int(Qt.Key_F1) if Qt.Key_F1<=key<=Qt.Key_F24 else int(key)
            if mods & Qt.MetaModifier or not (mods & (Qt.ControlModifier|Qt.AltModifier)) or vk>255:
                self.errors.append(name); continue
            if ctypes.windll.user32.RegisterHotKey(None,i,native,vk): self.ids[i]=name
            else: self.errors.append(name)
        return self.errors
    def clear(self):
        for i in self.ids: ctypes.windll.user32.UnregisterHotKey(None,i)
        self.ids={}
    def nativeEventFilter(self,eventType,message):
        msg=wintypes.MSG.from_address(int(message))
        if msg.message==0x0312 and int(msg.wParam) in self.ids:
            self.callback(self.ids[int(msg.wParam)]); return True,0
        return False,0

class Chart(QWidget):
    def __init__(self): super().__init__(); self.values=[]; self.setMinimumHeight(185); self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
    def paintEvent(self,e):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w,h=self.width(),self.height(); left=8; bottom=h-28; top=15; mid=(top+bottom)/2
        p.setPen(QPen(QColor('#2d3a51'),1)); p.drawLine(left,int(mid),w-8,int(mid))
        maxval=max([abs(v) for _,v in self.values]+[1]); step=(w-16)/max(1,len(self.values))
        p.setFont(QFont('Segoe UI',9))
        for i,(day,v) in enumerate(self.values):
            height=abs(v)/maxval*((bottom-top)/2-8)
            x=left+i*step+step*.18
            p.setPen(Qt.NoPen); p.setBrush(QColor('#6cdbc5' if v>=0 else '#fa8c9d'))
            p.drawRoundedRect(QRectF(x,mid-height if v>=0 else mid,step*.64,max(2,height)),4,4)
            p.setPen(QColor('#8e9cb4')); p.drawText(QRectF(left+i*step,bottom+5,step,20),Qt.AlignCenter,day.strftime('%d.%m'))
        if not any(v for _,v in self.values):
            p.setPen(QColor('#8798b4')); p.drawText(QRectF(0,25,w,45),Qt.AlignCenter,'График появится после изменения баланса')

class Overlay(QWidget):
    def __init__(self,owner,name,title,size):
        super().__init__(); self.owner=owner; self.name=name; self.editing=False; self.drag=None
        self.setWindowTitle('Nova Companion · '+title)
        self.setWindowFlags(Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer=QVBoxLayout(self); outer.setContentsMargins(0,0,0,0)
        self.frame=QFrame(); self.frame.setObjectName('card'); outer.addWidget(self.frame)
        self.body=QVBoxLayout(self.frame); self.body.setContentsMargins(16,12,16,12)
        bar=QHBoxLayout(); self.caption=label(title,'eyebrow'); bar.addWidget(self.caption); bar.addStretch()
        self.close_btn=button('×',self.hide); self.close_btn.setFixedSize(29,27); self.close_btn.setStyleSheet('padding: 0;'); bar.addWidget(self.close_btn)
        self.body.addLayout(bar)
        self.hint=label('НАСТРОЙКА · перетащи панель за верхний край','muted'); self.hint.setWordWrap(True); self.body.addWidget(self.hint); self.hint.hide()
        self.grip=QSizeGrip(self); self.grip.setFixedSize(18,18)
        self.resize(*size)
        geo=owner.store.get('overlay_'+name)
        if geo:
            self.setGeometry(*geo)
            if not any(s.availableGeometry().intersects(self.geometry()) for s in QApplication.screens()): self.move(80,80)
        else: self.move(60 if name=='finance' else 470,80 if name!='laws' else 180)
        self.setWindowOpacity(owner.store.get('opacity',92)/100)
    def set_edit(self,value):
        self.editing=value; self.hint.setVisible(value); self.grip.setVisible(value)
        visible=self.isVisible()
        self.setWindowFlag(Qt.WindowTransparentForInput,self.name=='finance' and not value)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus,self.name=='finance' and not value)
        self.close_btn.setVisible(self.name!='finance' or value)
        if visible: self.show()
    def mousePressEvent(self,e):
        if e.button()==Qt.LeftButton and (self.editing or self.name!='finance') and e.position().y()<65:
            self.drag=e.globalPosition().toPoint()-self.frameGeometry().topLeft(); e.accept()
    def mouseMoveEvent(self,e):
        if self.drag is not None: self.move(e.globalPosition().toPoint()-self.drag)
    def mouseReleaseEvent(self,e): self.drag=None; self.save()
    def resizeEvent(self,e):
        self.grip.move(self.width()-20,self.height()-20)
        super().resizeEvent(e)
    def save(self):
        g=self.geometry(); self.owner.store.set('overlay_'+self.name,[g.x(),g.y(),g.width(),g.height()])
    def closeEvent(self,e): self.save(); self.hide(); e.ignore()

class Main(QMainWindow):
    def __init__(self):
        super().__init__(); self.store=Store(DATA/'companion.sqlite3'); self.jobs=[]; self.candidate=None; self.repeats=0; self.pending=None; self.editing=False; self.shutting=False
        self.server=self.store.get('server','Mirage'); self.keys={**DEFAULT_KEYS,**self.store.get('hotkeys',{})}; self.last_seen=self.store.get('last_seen',{}); self.chat_busy=False
        self.setWindowTitle('Nova Companion'); self.resize(1160,820); self.setMinimumSize(980,700)
        root=QWidget(); root.setObjectName('root'); self.setCentralWidget(root); layout=QHBoxLayout(root); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        sidebar=QFrame(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(212); side=QVBoxLayout(sidebar); side.setContentsMargins(18,28,18,20); side.setSpacing(8)
        brand=label('NOVA <span style="color:#6cdbc5">/</span>','title'); side.addWidget(brand); side.addWidget(label('C O M P A N I O N','muted')); side.addSpacing(28)
        self.nav=[]; self.pages=QStackedWidget()
        for i,title in enumerate(['◈   Обзор','↗   История денег','▤   Заметки','§   Законка и агент','◇   Цены скупщиков','⚙   Настройки']):
            b=button(title,lambda checked=False,n=i:self.go(n)); b.setObjectName('nav'); b.setCheckable(True); side.addWidget(b); self.nav.append(b)
        side.addStretch(); side.addWidget(label('ТВОЙ СЕРВЕР','eyebrow')); self.server_box=QComboBox(); self.server_box.addItems(SERVERS); self.server_box.setCurrentText(self.server); self.server_box.currentTextChanged.connect(self.change_server); side.addWidget(self.server_box)
        from updates import version
        side.addSpacing(16); side.addWidget(label('LOCAL FIRST  ·  '+version(),'muted')); layout.addWidget(sidebar)
        right=QVBoxLayout(); right.setContentsMargins(30,26,30,20); right.setSpacing(18); layout.addLayout(right,1)
        header=QHBoxLayout(); self.heading=label('Обзор','title'); header.addWidget(self.heading); header.addStretch(); header.addWidget(button('Настроить оверлей',lambda:self.action('edit'))); header.addWidget(button('Скрыть окно',self.hide)); right.addLayout(header)
        self.scan_status=label('Подготовка распознавания…','muted'); self.scan_status.setWordWrap(True); right.addWidget(self.scan_status); right.addWidget(self.pages,1)
        self.make_dashboard(); self.make_history(); self.make_notes(); self.make_laws(); self.make_prices(); self.make_settings()
        self.make_overlays(); self.setup_tray()
        self.hotkeys=Hotkeys(self.action); QApplication.instance().installNativeEventFilter(self.hotkeys)
        errors=self.hotkeys.register(self.keys)
        if errors: self.hotkey_status.setText('Не удалось назначить: '+', '.join(errors)+'. Выбери другие сочетания.')
        self.scanner=Scanner(); self.scanner.result.connect(self.on_scan); self.scanner.status.connect(self.status); self.scanner.start()
        self.refresh_timer=QTimer(self); self.refresh_timer.timeout.connect(self.refresh); self.refresh_timer.start(15000)
        self.go(0); self.refresh(); self.finance.show(); self.finance.set_edit(False)
        self.update_result=None; self.update_stage=None
        self.update_timer=QTimer(self); self.update_timer.timeout.connect(lambda:self.check_updates(False)); self.update_timer.start(4*60*60*1000)
        if self.store.get('auto_updates',True) and not os.environ.get('NOVA_DATA_DIR'): QTimer.singleShot(4000,lambda:self.check_updates(False))
        self.backup_timer=QTimer(self); self.backup_timer.timeout.connect(self.backup_data); self.backup_timer.start(15*60*1000)
        QTimer.singleShot(500,self.backup_data)

    def backup_data(self):
        if self.shutting: return
        try: self.store.snapshot()
        except Exception as exc:
            (DATA/'backup-error.log').write_text(str(exc),encoding='utf-8')

    def page(self):
        p=QWidget(); l=QVBoxLayout(p); l.setContentsMargins(0,0,0,0); l.setSpacing(16); self.pages.addWidget(p); return l

    def make_dashboard(self):
        l=self.page(); welcome=label('Твой прогресс. Без лишнего шума.','muted'); l.addWidget(welcome)
        row=QHBoxLayout(); self.metrics=[]
        for title in ['Сегодня','Эта неделя','Этот месяц']:
            c,cl=card(); cl.addWidget(label(title,'muted')); v=label('0 $','value'); cl.addWidget(v); cl.addWidget(label('изменение общего баланса','muted')); row.addWidget(c); self.metrics.append(v)
        l.addLayout(row)
        c,cl=card(); top=QHBoxLayout(); top.addWidget(label('Динамика баланса')); top.addStretch(); top.addWidget(label('14 ДНЕЙ','eyebrow')); cl.addLayout(top); self.chart=Chart(); cl.addWidget(self.chart); l.addWidget(c,1)
        c,cl=card(); row=QHBoxLayout(); self.cash_label=label('Наличные  —'); self.bank_label=label('Банк  —'); self.total_label=label('Всего  —'); row.addWidget(self.cash_label); row.addWidget(self.bank_label); row.addWidget(self.total_label); cl.addLayout(row)
        self.last_label=label('Первый подтверждённый баланс станет точкой отсчёта.','muted'); self.last_label.setWordWrap(True); cl.addWidget(self.last_label); l.addWidget(c)
        self.pending_card,self.pending_layout=card(); self.pending_label=label(); self.pending_label.setWordWrap(True); self.pending_layout.addWidget(self.pending_label); row=QHBoxLayout(); row.addWidget(button('Учесть изменение',lambda:self.resolve_pending(False),True)); row.addWidget(button('Начать с этого баланса',lambda:self.resolve_pending(True))); row.addWidget(button('Отклонить',self.reject_pending)); self.pending_layout.addLayout(row); l.addWidget(self.pending_card); self.pending_card.hide()
        row=QHBoxLayout(); row.addWidget(button('Панель финансов',lambda:self.action('finance'))); row.addWidget(button('Быстрая заметка',lambda:self.action('notes'))); row.addWidget(button('Открыть законку',lambda:self.action('laws'))); l.addLayout(row)

    def make_history(self):
        l=self.page(); l.addWidget(label('Изменения наличных и банка. Переводы между ними дают результат 0.','muted'))
        self.history_table=QTableWidget(0,5); self.history_table.setHorizontalHeaderLabels(['Дата и время','Наличные','Банк','Результат','Тип']); self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.history_table.verticalHeader().hide(); self.history_table.setEditTriggers(QTableWidget.NoEditTriggers); self.history_table.setAlternatingRowColors(True); l.addWidget(self.history_table,1)
        row=QHBoxLayout(); row.addWidget(button('Экспорт CSV',self.export)); row.addWidget(button('Исправить последний баланс',self.correct_balance)); row.addStretch(); l.addLayout(row)

    def make_notes(self):
        l=self.page(); l.addWidget(label('Заметки сохраняются автоматически и отдельно для каждого сервера.','muted')); self.notes_edit=QTextEdit(); self.notes_edit.setPlaceholderText('Планы на игру, важные контакты, напоминания…'); self.notes_edit.setPlainText(self.store.note(self.server)); self.notes_edit.textChanged.connect(lambda:self.save_notes(self.notes_edit)); l.addWidget(self.notes_edit,1)

    def make_laws(self):
        l=self.page(); self.law_tabs=QTabWidget(); self.law_tabs.setStyleSheet('QTabWidget::pane { border:0; } QTabBar::tab { background:#182132;color:#94a6bf;padding:11px 24px;border-radius:7px;margin:0 8px 12px 0; } QTabBar::tab:selected { background:#24423f;color:#93edda; }'); l.addWidget(self.law_tabs,1)
        chat=QWidget(); cl=QVBoxLayout(chat); cl.setContentsMargins(0,0,0,0); cl.setSpacing(12); self.law_tabs.addTab(chat,'Диалог')
        row=QHBoxLayout(); row.addWidget(label('Задавай вопросы и уточняй по ходу разговора.','muted')); row.addStretch(); self.new_chat_button=button('Новый диалог',self.new_chat); row.addWidget(self.new_chat_button); cl.addLayout(row)
        self.chat_view=ChatView(); cl.addWidget(self.chat_view,1)
        row=QHBoxLayout(); self.law_query=ChatInput(); self.law_query.setPlaceholderText('Спроси или продолжи разговор…'); self.law_query.setText(self.store.get('chat_draft_'+self.server,'')); self.law_query.textChanged.connect(lambda:self.store.set('chat_draft_'+self.server,self.law_query.text())); self.law_query.returnPressed.connect(lambda:self.search_laws(True)); row.addWidget(self.law_query,1); self.ask_button=button('Отправить',lambda:self.search_laws(True),True); row.addWidget(self.ask_button); cl.addLayout(row)
        hint=label('Enter — отправить · Shift+Enter — новая строка','muted'); cl.addWidget(hint)
        library=QWidget(); ll=QVBoxLayout(library); ll.setContentsMargins(0,0,0,0); ll.setSpacing(12); self.law_tabs.addTab(library,'Библиотека')
        self.law_status=label('Загрузи документы выбранного сервера.','muted'); self.law_status.setWordWrap(True); ll.addWidget(self.law_status)
        row=QHBoxLayout(); self.sync_button=button('Обновить с форума',self.sync); row.addWidget(self.sync_button); row.addWidget(button('Импорт HTML / TXT',self.import_law)); row.addWidget(button('Импорт папки',self.import_folder)); row.addWidget(button('Открыть источники',self.open_sources)); ll.addLayout(row)
        row=QHBoxLayout(); self.document_box=QComboBox(); row.addWidget(self.document_box,1); row.addWidget(button('Читать документ',self.read_document)); ll.addLayout(row)
        row=QHBoxLayout(); self.library_query=QLineEdit(); self.library_query.setPlaceholderText('Поиск по тексту законов'); self.library_query.returnPressed.connect(lambda:self.search_laws(False)); row.addWidget(self.library_query,1); row.addWidget(button('Найти',lambda:self.search_laws(False))); ll.addLayout(row)
        self.law_output=QTextEdit(); self.law_output.setReadOnly(True); ll.addWidget(self.law_output,1); self.render_chat()

    def make_prices(self):
        from prices_ui import PricePanel,PriceWindow
        l=self.page();self.price_panel=PricePanel(self.server);l.addWidget(self.price_panel,1)
        self.price_window=PriceWindow(self.server,self)
        l.addWidget(button('Открыть отдельную панель · '+self.keys['prices'],self.price_window.present))

    def make_settings(self):
        l=self.page(); scroll=QScrollArea(); scroll.setWidgetResizable(True); content=QWidget(); content.setObjectName('root'); sl=QVBoxLayout(content); sl.setSpacing(15); scroll.setWidget(content); l.addWidget(scroll)
        c,cl=card(); cl.addWidget(label('Горячие клавиши','eyebrow')); self.key_edits={}
        for name,title in [('main','Главное окно'),('finance','Финансы: показать / скрыть'),('notes','Заметки: открыть / закрыть'),('laws','Законка: открыть / закрыть'),('edit','Настройка оверлея'),('prices','Цены: открыть / закрыть')]:
            row=QHBoxLayout(); row.addWidget(label(title)); row.addStretch(); edit=QKeySequenceEdit(QKeySequence(self.keys[name])); edit.setMaximumSequenceLength(1); row.addWidget(edit); self.key_edits[name]=edit; cl.addLayout(row)
        cl.addWidget(button('Сохранить сочетания',self.save_hotkeys)); self.hotkey_status=label('Используй Ctrl или Alt вместе с другой клавишей.','muted'); self.hotkey_status.setWordWrap(True); cl.addWidget(self.hotkey_status); sl.addWidget(c)
        c,cl=card(); cl.addWidget(label('Оверлей','eyebrow')); row=QHBoxLayout(); row.addWidget(label('Непрозрачность панелей')); self.opacity=QSlider(Qt.Horizontal); self.opacity.setRange(35,100); self.opacity.setValue(self.store.get('opacity',92)); self.opacity.valueChanged.connect(self.set_opacity); row.addWidget(self.opacity); cl.addLayout(row)
        row=QHBoxLayout(); row.addWidget(label('Масштаб текста панелей')); self.scale=QSlider(Qt.Horizontal); self.scale.setRange(80,170); self.scale.setValue(self.store.get('scale',100)); self.scale.valueChanged.connect(self.set_scale); row.addWidget(self.scale); cl.addLayout(row); cl.addWidget(button('Настроить расположение панелей',lambda:self.action('edit'))); sl.addWidget(c)
        c,cl=card(); cl.addWidget(label('Распознавание','eyebrow')); self.pause=QCheckBox('Приостановить считывание экрана'); self.pause.toggled.connect(self.pause_scan); cl.addWidget(self.pause); cl.addWidget(label('Считывание раз в ~2 секунды, только при активной игре.\nДва одинаковых результата подтверждают баланс. Снимки не сохраняются.','muted')); cl.addWidget(button('Проверить на скриншоте',self.test_screenshot)); sl.addWidget(c)
        from agent import available
        bundled=available()
        if bundled and not self.store.get('model',''): self.store.set('model','Встроенный Qwen · CPU')
        c,cl=card(); cl.addWidget(label('Бесплатный локальный агент','eyebrow')); desc=label('Встроенная модель отвечает на этом компьютере по загруженной законке.\nТакже можно подключить свою модель Ollama. Платных API нет.','muted'); desc.setWordWrap(True); cl.addWidget(desc); row=QHBoxLayout(); self.model_box=QComboBox(); self.model_box.setEditable(True); self.model_box.addItems(['','Встроенный Qwen · CPU'] if bundled else ['']); self.model_box.setCurrentText(self.store.get('model','')); self.model_box.currentTextChanged.connect(lambda s:self.store.set('model',s)); row.addWidget(self.model_box,1); row.addWidget(button('Найти модели Ollama',self.find_models)); cl.addLayout(row); self.model_status=label('Встроенный агент готов · загружается при первом вопросе' if bundled else 'Встроенная модель не найдена в папке agent','muted'); self.model_status.setWordWrap(True); cl.addWidget(self.model_status); cl.addWidget(button('Освободить память агента',self.unload_agent)); sl.addWidget(c)
        from updates import version,DEFAULT_REPO
        c,cl=card(); cl.addWidget(label('Обновления · '+version(),'eyebrow')); cl.addWidget(label('GitHub Releases · '+DEFAULT_REPO,'muted'))
        self.auto_updates=QCheckBox('Автоматически проверять новые версии'); self.auto_updates.setChecked(self.store.get('auto_updates',True)); self.auto_updates.toggled.connect(lambda value:self.store.set('auto_updates',value)); cl.addWidget(self.auto_updates)
        self.update_status=label('Обновляются приложение и общая законка. Личные данные сохраняются.','muted'); self.update_status.setWordWrap(True); cl.addWidget(self.update_status)
        row=QHBoxLayout(); self.update_check=button('Проверить обновления',lambda:self.check_updates(True)); row.addWidget(self.update_check); self.update_install=button('Установить и перезапустить',self.install_update,True); self.update_install.setEnabled(False); row.addWidget(self.update_install); cl.addLayout(row)
        from publisher import KEY
        if KEY.exists(): cl.addWidget(button('Выпустить обновление для всех',self.open_publisher))
        sl.addWidget(c)
        c,cl=card(); cl.addWidget(label('Данные хранятся на этом компьютере','eyebrow')); path=label(str(DATA),'muted'); path.setWordWrap(True); cl.addWidget(path); cl.addWidget(button('Открыть папку данных',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA))))); sl.addWidget(c)

    def make_overlays(self):
        self.finance=Overlay(self,'finance','◈  ФИНАНСЫ · '+self.server,(340,260)); self.finance_value=label('—','value'); self.finance.body.addWidget(self.finance_value); self.finance_detail=label('Наличные —    Банк —','muted'); self.finance_detail.setWordWrap(True); self.finance.body.addWidget(self.finance_detail); self.finance_today=label('Сегодня  0 $'); self.finance.body.addWidget(self.finance_today); self.finance_periods=label('Неделя  0 $  ·  Месяц  0 $','muted'); self.finance_periods.setWordWrap(True); self.finance.body.addWidget(self.finance_periods); self.finance_state=label('Ожидание баланса','muted'); self.finance_state.setWordWrap(True); self.finance.body.addWidget(self.finance_state)
        self.note_overlay=Overlay(self,'notes','▤  ЗАМЕТКИ · '+self.server,(380,330)); self.quick_note=QTextEdit(); self.quick_note.setPlaceholderText('Быстрая запись…'); self.quick_note.setPlainText(self.store.note(self.server)); self.quick_note.textChanged.connect(lambda:self.save_notes(self.quick_note)); self.note_overlay.body.addWidget(self.quick_note,1)
        self.law_overlay=Overlay(self,'laws','§  ДИАЛОГ · '+self.server,(600,500)); self.quick_output=ChatView(); self.law_overlay.body.addWidget(self.quick_output,1); row=QHBoxLayout(); self.quick_query=QLineEdit(); self.quick_query.setPlaceholderText('Уточни или задай вопрос…'); self.quick_query.returnPressed.connect(lambda:self.quick_search(True)); row.addWidget(self.quick_query,1); self.quick_send=button('Отправить',lambda:self.quick_search(True)); row.addWidget(self.quick_send); self.law_overlay.body.addLayout(row); self.render_chat()
        self.overlays=[self.finance,self.note_overlay,self.law_overlay]
        for o in self.overlays: o.set_edit(False)
        self.set_scale(self.store.get('scale',100))

    def setup_tray(self):
        pix=QPixmap(64,64); pix.fill(QColor('#111725')); p=QPainter(pix); p.setPen(QColor('#6cdbc5')); p.setFont(QFont('Segoe UI',33,QFont.Bold)); p.drawText(pix.rect(),Qt.AlignCenter,'N'); p.end(); icon=QIcon(pix); self.setWindowIcon(icon)
        self.tray=QSystemTrayIcon(icon,self); self.tray.setToolTip('Nova Companion'); menu=QMenu(self); self.tray_menu=menu
        for text,name in [('Главное окно','main'),('Финансы','finance'),('Заметки','notes'),('Законка','laws'),('Настроить оверлей','edit')]: menu.addAction(text,lambda checked=False,n=name:self.action(n))
        menu.addAction('Цены скупщиков',lambda:self.action('prices'))
        menu.addSeparator(); menu.addAction('Завершить работу',self.quit_app); self.tray.setContextMenu(menu); self.tray.activated.connect(lambda reason:self.action('main') if reason==QSystemTrayIcon.DoubleClick else None); self.tray.show()

    def go(self,n):
        self.pages.setCurrentIndex(n); self.heading.setText(['Обзор','История денег','Заметки','Законка и агент','Цены скупщиков','Настройки'][n])
        self.scan_status.setVisible(n in (0,1,5))
        for i,b in enumerate(self.nav): b.setChecked(i==n)

    def action(self,name):
        if name=='main':
            if self.isVisible() and self.isActiveWindow(): self.hide()
            else: self.showNormal(); self.raise_(); self.activateWindow()
        elif name=='prices':
            self.price_window.toggle()
        elif name=='edit':
            self.editing=not self.editing
            if self.editing: self.pre_edit=[o.isVisible() for o in self.overlays]
            for i,o in enumerate(self.overlays):
                o.set_edit(self.editing)
                if self.editing: o.show()
                else: o.save(); o.setVisible(self.pre_edit[i])
            self.tray.showMessage('Оверлей','Режим настройки включён. Повтори сочетание для сохранения.' if self.editing else 'Расположение панелей сохранено.',QSystemTrayIcon.Information,2000)
        else:
            o={'finance':self.finance,'notes':self.note_overlay,'laws':self.law_overlay}[name]
            o.setVisible(not o.isVisible())
            if name!='finance' and o.isVisible():
                o.raise_(); o.activateWindow()
                (self.quick_note if name=='notes' else self.quick_query).setFocus()

    def change_server(self,name):
        self.server=name; self.store.set('server',name); self.candidate=None; self.repeats=0; self.pending=None; self.pending_card.hide()
        self.law_query.blockSignals(True); self.law_query.setText(self.store.get('chat_draft_'+name,'')); self.law_query.blockSignals(False)
        for edit in [self.notes_edit,self.quick_note]: edit.blockSignals(True); edit.setPlainText(self.store.note(name)); edit.blockSignals(False)
        for o,title in [(self.finance,'◈  ФИНАНСЫ'),(self.note_overlay,'▤  ЗАМЕТКИ'),(self.law_overlay,'§  ЗАКОНКА')]: o.caption.setText(title+' · '+name)
        self.price_panel.set_server(name); self.price_window.panel.set_server(name)
        self.law_output.clear(); self.quick_output.clear(); self.refresh(); self.status('Сервер изменён · ожидаю баланс'); self.show_library(); self.render_chat()

    def save_notes(self,source):
        text=source.toPlainText(); self.store.save_note(self.server,text)
        if not hasattr(self,'quick_note'): return
        other=self.quick_note if source is self.notes_edit else self.notes_edit
        if other.toPlainText()!=text: other.blockSignals(True); other.setPlainText(text); other.blockSignals(False)

    def status(self,s):
        self.scan_status.setText(('ПАУЗА · ' if self.pause.isChecked() else '')+s)
        self.finance_state.setText(s)
        if 'скрыты' in s or 'Ожидание' in s: self.candidate=None; self.repeats=0

    def on_scan(self,pair):
        if self.pause.isChecked(): return
        if self.candidate==pair: self.repeats+=1
        else: self.candidate=pair; self.repeats=1
        if self.repeats<2: self.status('Проверяю найденные цифры…'); return
        old=self.store.last(self.server)
        if self.pending:
            if pair!=self.pending: self.pending=None; self.pending_card.hide()
            else: return
        if old:
            delta=sum(pair)-old['cash']-old['bank']; age=(dt.datetime.now().astimezone()-dt.datetime.fromisoformat(self.last_seen.get(self.server,old['stamp']))).total_seconds()
            if delta and (abs(delta)>2_000_000 or age>1800):
                self.pending=pair; self.pending_label.setText(f'Нужно подтверждение: {money(sum(pair))}, изменение {money(delta)}. Был долгий перерыв или крупное изменение.'); self.pending_card.show(); self.status('Новый баланс ожидает подтверждения в главном окне'); return
        self.last_seen[self.server]=dt.datetime.now().astimezone().isoformat(); self.store.set('last_seen',self.last_seen)
        self.store.record(self.server,*pair); self.status('● Баланс подтверждён · '+dt.datetime.now().strftime('%H:%M:%S')); self.refresh()

    def resolve_pending(self,baseline):
        if self.pending: self.store.record(self.server,*self.pending,baseline=baseline)
        self.pending=None; self.pending_card.hide(); self.refresh()
    def reject_pending(self):
        self.pending=None; self.pending_card.hide(); self.pause.setChecked(True); self.status('Считывание приостановлено после отклонения баланса')

    def refresh(self):
        totals=self.store.totals(self.server)
        for v,n in zip(self.metrics,totals): v.setText(('+' if n>0 else '')+money(n)); v.setStyleSheet('color: '+('#fa8c9d' if n<0 else '#6cdbc5')+';')
        self.chart.values=self.store.days(self.server); self.chart.update(); old=self.store.last(self.server)
        self.finance_today.setText('Сегодня  '+('+' if totals[0]>0 else '')+money(totals[0]))
        self.finance_periods.setText('Неделя  '+money(totals[1])+'  ·  Месяц  '+money(totals[2]))
        if old:
            self.cash_label.setText('Наличные  '+money(old['cash'])); self.bank_label.setText('Банк  '+money(old['bank'])); self.total_label.setText('Всего  '+money(old['cash']+old['bank'])); self.last_label.setText('Последнее изменение: '+dt.datetime.fromisoformat(old['stamp']).strftime('%d.%m.%Y %H:%M:%S')); self.finance_value.setText(money(old['cash']+old['bank'])); self.finance_detail.setText('Наличные  '+money(old['cash'])+'\nБанк           '+money(old['bank']))
        else:
            for w,title in [(self.cash_label,'Наличные'),(self.bank_label,'Банк'),(self.total_label,'Всего')]: w.setText(title+'  —')
            self.finance_value.setText('—'); self.finance_detail.setText('Жду обе суммы на экране'); self.last_label.setText('Первое подтверждённое считывание задаст начальный баланс.')
        rows=self.store.history(self.server); self.history_table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,text in enumerate([dt.datetime.fromisoformat(r['stamp']).strftime('%d.%m %H:%M:%S'),money(r['cash']),money(r['bank']),('+' if r['delta']>0 else '')+money(r['delta']),'Точка отсчёта' if r['kind']=='baseline' else 'Считывание']):
                item=QTableWidgetItem(text)
                if j==3: item.setForeground(QColor('#fa8c9d' if r['delta']<0 else '#6cdbc5'))
                self.history_table.setItem(i,j,item)
        documents=self.store.laws(self.server); count=len(documents); selected=self.document_box.currentData(); self.document_box.blockSignals(True); self.document_box.clear()
        for doc in documents: self.document_box.addItem(doc['title'],doc['id'])
        idx=self.document_box.findData(selected)
        if idx>=0: self.document_box.setCurrentIndex(idx)
        self.document_box.blockSignals(False)
        self.law_status.setText(f'{self.server} · документов: {count}. Ответы по сохранённым копиям; дату сверяй с источником.')

    def run_job(self,fn,done,fail=None):
        j=Job(fn); self.jobs.append(j); j.done.connect(done); j.failed.connect(fail or (lambda s:QMessageBox.information(self,'Nova Companion',s))); j.finished.connect(lambda:self.jobs.remove(j) if j in self.jobs else None); j.start(); return j

    def sync(self):
        server=self.server; self.sync_button.setEnabled(False)
        def finish(docs):
            for title,url,body in docs: self.store.save_law(server,title,url,body)
            self.sync_button.setEnabled(True); self.refresh(); self.show_library()
        def fail(s): self.sync_button.setEnabled(True); self.law_status.setText(s); QMessageBox.information(self,'Обновление источников',s)
        j=self.run_job(lambda progress:sync_forum(server,progress),finish,fail); j.progress.connect(self.law_status.setText)

    def import_law(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Импорт законки','','Документы (*.html *.htm *.txt)')
        for filename in paths:
            path=Path(filename); raw=path.read_bytes()
            try: text=raw.decode('utf-8-sig')
            except UnicodeDecodeError: text=raw.decode('cp1251',errors='replace')
            title,body=extract_document(text) if path.suffix.lower()!='.txt' else (path.stem,text)
            if len(body.strip())<40: QMessageBox.information(self,'Импорт','Слишком мало текста: '+path.name); continue
            if any(t in body.lower() for t in ['checking your browser','react labs security']): QMessageBox.information(self,'Импорт','Это страница проверки браузера. Сохрани открытую статью законки.'); continue
            url,ok=QInputDialog.getText(self,'Источник документа','Ссылка на статью (можно оставить пустой):')
            if not ok: continue
            if url and not url.startswith(('https://','http://')): QMessageBox.information(self,'Источник','Нужна ссылка https:// или http://'); continue
            self.store.save_law(self.server,title,url or path.resolve().as_uri(),body)
        self.refresh(); self.show_library()

    def show_library(self):
        rows=self.store.laws(self.server)
        if not rows: self.law_output.setPlainText('Законка ещё не загружена.\n\nНажми «Обновить с форума» или импортируй HTML / TXT.\nТексты и ответы разных серверов хранятся отдельно.'); return
        self.law_output.setHtml('<h2>Библиотека · '+html.escape(self.server)+'</h2>'+''.join('<p><b>'+html.escape(r['title'])+'</b><br>'+html.escape(r['url'])+'<br><span style="color:#95aac5">Копия: '+html.escape(r['stamp'])+'</span></p>' for r in rows))

    def import_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'Выбрать папку законки для '+self.server)
        if not folder: return
        files=[p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in ('.html','.htm','.txt')]
        imported=0; errors=[]
        for path in files:
            try:
                title,url,body=read_saved_document(path)
                if len(body.strip())<40: raise ValueError('слишком мало текста')
                self.store.save_law(self.server,title,url,body); imported+=1
            except Exception as exc: errors.append(path.name+': '+str(exc))
        self.refresh(); self.show_library()
        QMessageBox.information(self,'Импорт папки',f'{self.server}: загружено документов {imported}.'+ ('\n\nНе загружены:\n'+'\n'.join(errors[:8]) if errors else ''))

    def read_document(self):
        doc=self.store.db.execute('SELECT * FROM laws WHERE id=? AND server=?',(self.document_box.currentData(),self.server)).fetchone()
        if not doc: self.show_library(); return
        self.law_output.setPlainText(doc['title']+'\n'+doc['url']+'\nКопия: '+doc['stamp']+'\n\n'+doc['body'])

    def open_sources(self):
        for url in SERVERS[self.server]: QDesktopServices.openUrl(QUrl(url))

    def search_laws(self,agent=False,query=None,output=None):
        if agent: self.submit_chat(self.law_query.text() if query is None else query); return
        query=self.library_query.text() if query is None else query; output=output or self.law_output
        hits=self.store.search(self.server,query,research=agent)
        if not agent:
            if not hits: output.setPlainText('В загруженной законке выбранного сервера совпадений нет. Проверь библиотеку или измени запрос.'); return
            result='<h3>'+html.escape(self.server)+' · найденные выдержки</h3>'
            for i,(_,r,chunk) in enumerate(hits):
                result+=f'<h4>[{i+1}] '+html.escape(r['title'])+' · п. '+html.escape(r.get('reference',''))+'</h4><p>'+html.escape(r.get('section',''))+'</p><p>'+html.escape(chunk).replace('\n','<br>')+'</p><p style="color:#8e9cb4">'+html.escape(r['url'])+'<br>Копия: '+html.escape(r['stamp'])+'</p><hr>'
            output.setHtml(result); return

    def chat_history(self,server=None): return self.store.get('chat_history_'+(server or self.server),[])

    def render_chat(self):
        messages=self.chat_history(); pending=self.chat_busy and getattr(self,'chat_pending_server',None)==self.server
        self.chat_view.set_messages(messages,pending)
        if hasattr(self,'quick_output'): self.quick_output.set_messages(messages,pending)

    def new_chat(self):
        if self.chat_busy: return
        import uuid
        previous=self.chat_history()
        if previous: self.store.set('chat_archive_'+self.server+'_'+uuid.uuid4().hex,previous)
        self.store.set('chat_history_'+self.server,[]); self.law_query.clear(); self.render_chat(); self.law_query.setFocus()

    def set_chat_busy(self,value):
        self.chat_busy=value; self.ask_button.setEnabled(not value); self.new_chat_button.setEnabled(not value)
        if hasattr(self,'quick_send'): self.quick_send.setEnabled(not value)

    def submit_chat(self,question):
        question=question.strip()
        if not question or self.chat_busy: return
        from conversation import answer_turn,effective_query,clean_text
        from legal import evidence_pack
        from services import context_budget
        server=self.server; history=self.chat_history(server); before=list(history)
        history.append({'role':'user','content':question}); self.store.set('chat_history_'+server,history[-40:])
        self.law_query.clear()
        if hasattr(self,'quick_query'): self.quick_query.clear()
        self.chat_pending_server=server; self.set_chat_busy(True); self.render_chat()
        response={'answer':'Опиши, пожалуйста, ситуацию несколькими сообщениями: сначала место, участников и само требование, затем остальные обстоятельства. Так я смогу уточнить важные детали и ответить по статьям.','details':'','sources':[]} if len(question)>5000 else answer_turn(self.store,server,question,before)
        expanded=effective_query(question,before); hits=[] if response else self.store.search(server,expanded,True); model=self.store.get('model','')
        def generate(progress):
            if response: return response
            raw=local_answer(question,hits,model,history=before,context_query=expanded)
            parts=raw.split('\n\nИсточники и подробности\n\n',1)
            selected=evidence_pack(hits,expanded,budget=context_budget(model,question))
            sources=[{'number':i+1,'title':meta['title'],'reference':'п. '+meta.get('reference',''),'url':meta['url']} for i,(meta,body) in enumerate(selected)]
            return {'answer':clean_text(parts[0]),'details':parts[1] if len(parts)>1 else '', 'sources':sources}
        def done(result):
            import re
            cited={int(v) for v in re.findall(r'\[(\d+)\]',result['answer'])}
            sources=[s for s in result.get('sources',[]) if s.get('number') in cited]
            messages=self.chat_history(server); messages.append({'role':'assistant','content':clean_text(result['answer']),'details':clean_text(result.get('details','')),'sources':sources})
            self.store.set('chat_history_'+server,messages[-40:]); self.set_chat_busy(False); self.render_chat()
        def fail(message):
            messages=self.chat_history(server); messages.append({'role':'assistant','content':'Сейчас не удалось подготовить ответ. Попробуй ещё раз или открой статьи в библиотеке.','details':message}); self.store.set('chat_history_'+server,messages[-40:]); self.set_chat_busy(False); self.render_chat()
        self.run_job(generate,done,fail)

    def quick_search(self,agent=True): self.submit_chat(self.quick_query.text())

    def find_models(self):
        import requests
        def done(data):
            from agent import available
            choices=(['Встроенный Qwen · CPU'] if available() else [])+data
            selected=self.store.get('model',''); self.model_box.blockSignals(True); self.model_box.clear(); self.model_box.addItem(''); self.model_box.addItems(choices); self.model_box.setCurrentText(selected if selected in choices else (choices[0] if choices else '')); self.model_box.blockSignals(False); self.store.set('model',self.model_box.currentText()); self.model_status.setText(f'Найдено моделей Ollama: {len(data)}')
        self.run_job(lambda progress:[m['name'] for m in requests.get('http://127.0.0.1:11434/api/tags',timeout=4).json().get('models',[])],done,lambda s:self.model_status.setText('Ollama не найден. Можно использовать встроенную модель.'))

    def unload_agent(self):
        if self.jobs: self.model_status.setText('Дождись завершения текущего ответа.'); return
        from agent import stop
        stop(); self.model_status.setText('Память освобождена. Модель запустится при следующем вопросе.')

    def check_updates(self,manual=False):
        if not manual and not self.store.get('auto_updates',True): return
        if not self.update_check.isEnabled(): return
        self.update_check.setEnabled(False); self.update_status.setText('Проверяю опубликованные версии…')
        from updates import check_release
        def done(result):
            self.update_check.setEnabled(True); self.update_result=result if result.get('available') else None; self.update_status.setText(result['message']); self.update_install.setEnabled(bool(self.update_result))
            if self.update_result:
                ver=result['payload']['version']
                if self.store.get('notified_version')!=ver:
                    self.tray.showMessage('Nova Companion','Доступна версия '+ver+'. Установка — в настройках.',QSystemTrayIcon.Information,5000); self.store.set('notified_version',ver)
        def fail(message): self.update_check.setEnabled(True); self.update_status.setText('Не удалось проверить обновление: '+message)
        self.run_job(lambda progress:check_release(),done,fail)

    def install_update(self):
        if self.update_stage: self.finish_update(); return
        if not self.update_result: return
        if self.jobs: self.update_status.setText('Дождись завершения текущего ответа или загрузки.'); return
        self.update_install.setEnabled(False); self.update_check.setEnabled(False)
        from updates import download_release
        def done(stage):
            self.update_stage=stage; self.update_status.setText('Подпись автора и архив проверены. Подготовка к перезапуску…'); QTimer.singleShot(500,self.finish_update)
        def fail(message): self.update_install.setEnabled(True); self.update_check.setEnabled(True); self.update_status.setText('Обновление не установлено: '+message)
        job=self.run_job(lambda progress:download_release(self.update_result,progress),done,fail); job.progress.connect(self.update_status.setText)

    def finish_update(self):
        if self.jobs:
            self.update_install.setEnabled(True); self.update_install.setText('Перезапустить и установить'); self.update_status.setText('Пакет готов. Заверши текущие задачи и нажми установку.'); return
        self.scanner.stopping=True; self.scanner.wait(15000)
        if self.scanner.isRunning(): self.update_status.setText('Жду завершения распознавания. Повтори установку через несколько секунд.'); self.update_install.setEnabled(True); return
        from updates import launch_installer
        launch_installer(self.update_stage); self.quit_app()

    def open_publisher(self):
        import subprocess
        from updates import root_dir
        root=root_dir(); subprocess.Popen([str(root/'runtime'/'pythonw.exe'),str(root/'source'/'publisher.py'),'--gui'],cwd=str(root),creationflags=subprocess.CREATE_NO_WINDOW)

    def save_hotkeys(self):
        keys={name:edit.keySequence().toString() for name,edit in self.key_edits.items()}
        if len(set(keys.values()))!=len(keys): self.hotkey_status.setText('Сочетания должны различаться.'); return
        errors=self.hotkeys.register(keys)
        if errors: self.hotkeys.register(self.keys); self.hotkey_status.setText('Не удалось назначить: '+', '.join(errors)+'. Старые сочетания сохранены.'); return
        self.keys=keys; self.store.set('hotkeys',keys); self.hotkey_status.setText('Сочетания сохранены.')

    def set_opacity(self,value):
        self.store.set('opacity',value)
        for o in getattr(self,'overlays',[]): o.setWindowOpacity(value/100)
    def set_scale(self,value):
        self.store.set('scale',value)
        for o in getattr(self,'overlays',[]):
            o.setStyleSheet('QTextEdit,QLineEdit,QPushButton { font-size: '+str(round(13*value/100))+'px; }')
            for w in o.findChildren(QLabel): w.setStyleSheet('font-size: '+str(round((27 if w.objectName()=='value' else 12)*value/100))+'px;')
    def pause_scan(self,value):
        if hasattr(self,'scanner'): self.scanner.enabled=not value
        self.candidate=None; self.repeats=0
        self.status('Считывание приостановлено' if value else 'Ожидаю игровой интерфейс')

    def test_screenshot(self):
        path,_=QFileDialog.getOpenFileName(self,'Проверить распознавание','','Изображения (*.png *.jpg *.bmp)')
        if not path: return
        from PIL import Image
        from vision import read_image
        def done(result):
            pair,lines=result
            QMessageBox.information(self,'Проверка распознавания',('Наличные: '+money(pair[0])+'\nБанк: '+money(pair[1])+'\nВсего: '+money(sum(pair))+'\n\nТест не записывает баланс в историю.') if pair else 'Обе суммы не найдены. Нужен снимок с видимым игровым интерфейсом.')
        self.run_job(lambda progress:read_image(Image.open(path).convert('RGB')),done)

    def correct_balance(self):
        old=self.store.last(self.server)
        if not old: return
        raw,ok=QInputDialog.getText(self,'Исправление','Наличные и банк через пробел:',text=f'{old["cash"]} {old["bank"]}')
        if not ok: return
        try:
            cash,bank=map(int,raw.split())
            if min(cash,bank)<0: raise ValueError()
        except ValueError: QMessageBox.information(self,'Исправление','Введи два целых неотрицательных числа через пробел.'); return
        prev=self.store.db.execute('SELECT * FROM readings WHERE server=? AND id<? ORDER BY id DESC LIMIT 1',(self.server,old['id'])).fetchone()
        delta=0 if old['kind']=='baseline' or prev is None else cash+bank-prev['cash']-prev['bank']
        self.store.db.execute('UPDATE readings SET cash=?,bank=?,delta=? WHERE id=?',(cash,bank,delta,old['id'])); self.store.db.commit(); self.refresh()

    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'Экспорт истории',f'Nova-{self.server}.csv','CSV (*.csv)')
        if not path: return
        with open(path,'w',newline='',encoding='utf-8-sig') as f:
            writer=csv.writer(f,delimiter=';'); writer.writerow(['Сервер','Дата','Наличные','Банк','Изменение','Тип'])
            for r in self.store.db.execute('SELECT * FROM readings WHERE server=? ORDER BY id',(self.server,)): writer.writerow([r['server'],r['stamp'],r['cash'],r['bank'],r['delta'],r['kind']])

    def closeEvent(self,e):
        if self.shutting: e.accept()
        else: self.hide(); e.ignore()

    def quit_app(self):
        if self.jobs:
            QMessageBox.information(self,'Nova Companion','Дождись завершения текущего обновления или ответа агента.'); return
        self.shutting=True; self.scanner.stopping=True; self.scanner.wait(15000)
        if self.scanner.isRunning():
            self.shutting=False; QMessageBox.information(self,'Nova Companion','Распознавание ещё завершается. Повтори выход через несколько секунд.'); return
        self.hotkeys.clear()
        from agent import stop
        stop()
        for o in self.overlays: o.save(); o.hide()
        try: self.store.snapshot()
        except Exception: pass
        self.tray.hide(); QApplication.instance().quit()

def main():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True); kernel.CreateMutexW.restype=wintypes.HANDLE
    mutex=kernel.CreateMutexW(None,False,'Local\\NovaCompanion.Player.01')
    if ctypes.get_last_error()==183:
        ctypes.windll.user32.MessageBoxW(None,'Nova Companion уже запущен. Открой окно через значок рядом с часами или назначенное сочетание.','Nova Companion',0x40)
        return
    ctypes.windll.kernel32.SetErrorMode(0x0001|0x0002)
    app=QApplication(sys.argv); app.setApplicationName('Nova Companion'); app.setQuitOnLastWindowClosed(False); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    from services import install_bundled_library
    seed_store=Store(DATA/'companion.sqlite3'); install_bundled_library(seed_store); seed_store.db.close()
    window=Main()
    if '--background' not in sys.argv: window.show()
    window.show_library()
    if '--ask' in sys.argv:
        window.server_box.setCurrentText('Mirage'); window.go(3)
        window.law_query.setText(sys.argv[sys.argv.index('--ask')+1])
        QTimer.singleShot(500,lambda:window.search_laws(True))
    if '--preview' in sys.argv:
        QTimer.singleShot(5000 if '--ask' in sys.argv else 2000,lambda:window.grab().save(str(Path(sys.argv[sys.argv.index('--preview')+1]))))
    sys.exit(app.exec())

if __name__=='__main__':
    try:
        if '--diagnose' in sys.argv:
            from PIL import Image
            from vision import read_image
            from agent import available
            i=sys.argv.index('--diagnose'); pair,lines=read_image(Image.open(sys.argv[i+1]).convert('RGB'))
            Path(sys.argv[i+2]).write_text(json.dumps({'balance':pair,'ocr_lines':lines,'local_model':available()},ensure_ascii=False,indent=2),encoding='utf-8')
        else: main()
    except Exception as exc:
        DATA.mkdir(parents=True,exist_ok=True); (DATA/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None,'Не удалось запустить Nova Companion.\n\n'+str(exc)+'\n\nПодробности: '+str(DATA/'error.log'),'Nova Companion',0x10)
        raise
