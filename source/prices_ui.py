"""Shared, source-linked historical price records. No Discord account access."""
import datetime as dt
import json
from pathlib import Path
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QColor,QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,
    QComboBox,QTableWidget,QTableWidgetItem,QHeaderView,QPushButton,QFrame)

def load_prices():
    return json.loads(Path(__file__).with_name('prices-data.json').read_text(encoding='utf-8'))

def filtered_records(data,query='',buyer='Все скупщики'):
    query=query.strip().casefold()
    records=[]
    for record in data['maxima']:
        hits=record['occurrences']
        if buyer!='Все скупщики':
            # Only the overall record's occurrences are stored, not buyer-specific maxima.
            hits=[h for h in hits if h['buyer']==buyer]
        if query and query not in record['item'].casefold():continue
        if hits:records.append(hits[0])
    return records

class NumericItem(QTableWidgetItem):
    def __init__(self,value):
        super().__init__('—' if value is None else f'{value:,}'.replace(',',' '))
        self.setData(Qt.UserRole,-1 if value is None else value)
        self.setTextAlignment(Qt.AlignRight|Qt.AlignVCenter)
    def __lt__(self,other):return self.data(Qt.UserRole)<other.data(Qt.UserRole)

class PricePanel(QWidget):
    def __init__(self,server='Mirage',parent=None):
        super().__init__(parent);self.data=load_prices();self.server=server
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        bar=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('Найти товар: алмаз, рыба, мясо…');bar.addWidget(self.search,1)
        self.buyer=QComboBox();self.buyer.addItems(['Все скупщики','№1','№2','№3','Продукты']);self.buyer.setToolTip('Показать товары, у которых общий рекорд найден у этого скупщика');bar.addWidget(self.buyer);layout.addLayout(bar)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Товар','Максимум, $','В скобках, $','Скупщик','Дата · МСК'])
        self.table.verticalHeader().hide();self.table.setEditTriggers(QTableWidget.NoEditTriggers);self.table.setSelectionBehavior(QTableWidget.SelectRows);self.table.setSelectionMode(QTableWidget.SingleSelection);self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        for col in range(1,5):self.table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeToContents)
        self.table.setSortingEnabled(True);layout.addWidget(self.table,1)
        self.detail=QLabel('Выбери товар, чтобы увидеть источник рекорда.');self.detail.setWordWrap(True);layout.addWidget(self.detail)
        row=QHBoxLayout();self.source=QPushButton('Открыть публикацию в Discord');self.source.setEnabled(False);self.source.clicked.connect(self.open_source);row.addWidget(self.source);row.addStretch();self.count=QLabel();row.addWidget(self.count);layout.addLayout(row)
        self.note=QLabel('Это найденные исторические максимумы, не текущие цены. Цены в скобках сохранены как в публикациях. Автоматического подключения к Discord нет.');self.note.setObjectName('muted');self.note.setWordWrap(True);layout.addWidget(self.note)
        self.search.textChanged.connect(self.refresh);self.buyer.currentTextChanged.connect(self.refresh);self.table.itemSelectionChanged.connect(self.selection_changed)
        self.refresh()

    def set_server(self,server):self.server=server;self.refresh()

    def refresh(self,*args):
        available=self.server==self.data['server']
        start=dt.datetime.fromisoformat(self.data['start']).strftime('%d.%m.%Y')
        end=dt.datetime.fromisoformat(self.data['end']).strftime('%d.%m.%Y %H:%M')
        self.summary.setText(f'МАКСИМУМЫ MIRAGE  ·  {start} — {end} МСК\nПроверено {self.data["messages"]} из {self.data["search_results"]} сообщений. История неполная; рекорды за всё время не подтверждены.' if available else f'{self.server}: история цен ещё не добавлена.')
        self.search.setEnabled(available);self.buyer.setEnabled(available);self.note.setVisible(available)
        records=filtered_records(self.data,self.search.text(),self.buyer.currentText()) if available else []
        self.table.setSortingEnabled(False);self.table.setRowCount(0)
        for r in records:
            n=self.table.rowCount();self.table.insertRow(n);name=QTableWidgetItem(r['item']);name.setData(Qt.UserRole,r);self.table.setItem(n,0,name)
            self.table.setItem(n,1,NumericItem(r['price']));self.table.setItem(n,2,NumericItem(r['bracket']))
            self.table.item(n,1).setForeground(QColor('#6cdbc5'))
            self.table.setItem(n,3,QTableWidgetItem(r['buyer']));date=dt.datetime.fromisoformat(r['date']);date_item=QTableWidgetItem(date.strftime('%d.%m %H:%M'));date_item.setToolTip(date.strftime('%d.%m.%Y %H:%M')+' МСК');self.table.setItem(n,4,date_item)
        self.table.setSortingEnabled(True);self.count.setText(f'Товаров: {len(records)}');self.selection_changed()

    def selected_record(self):
        selected=self.table.selectionModel().selectedRows()
        return self.table.item(selected[0].row(),0).data(Qt.UserRole) if selected else None

    def selection_changed(self):
        r=self.selected_record();self.source.setEnabled(r is not None)
        self.detail.setText(f'{r["item"]} · {r["buyer"]} · {dt.datetime.fromisoformat(r["date"]).strftime("%d.%m.%Y %H:%M")} МСК\nПри одинаковой цене показано одно из подтверждённых совпадений.' if r else 'Выбери товар, чтобы увидеть источник рекорда.')

    def open_source(self):
        r=self.selected_record()
        if r:QDesktopServices.openUrl(QUrl(r['url']))

class PriceWindow(QWidget):
    def __init__(self,server,parent=None):
        super().__init__(parent,Qt.Tool|Qt.WindowStaysOnTopHint);self.setWindowTitle('Nova Companion · Цены скупщиков');self.setObjectName('root');self.resize(850,680);self.setMinimumSize(650,480)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,20,20,20);self.panel=PricePanel(server);layout.addWidget(self.panel)
    def closeEvent(self,event):self.hide();event.ignore()
