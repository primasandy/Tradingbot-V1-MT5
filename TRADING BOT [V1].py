import sys
import MetaTrader5 as mt5
import pandas as pd
import time
import numpy as np
import datetime
import requests
import json
import os # Import modul os untuk manipulasi jalur file

from ta.momentum import RSIIndicator
from ta.volume import OnBalanceVolumeIndicator
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QTextEdit,
    QGroupBox, QGridLayout, QSizePolicy, QLineEdit, QDoubleSpinBox, QComboBox,
    QDialog, QDialogButtonBox, QMessageBox
)
from PyQt6.QtCore import QTimer, Qt

from ta.trend import MACD, EMAIndicator, SMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange

# Inisialisasi koneksi MT5
# Penting: Pastikan MetaTrader 5 sedang berjalan dan Anda sudah login ke akun.
if not mt5.initialize():
    print("FATAL ERROR: Gagal terhubung ke MetaTrader 5. Pastikan MT5 berjalan dan akun login.")
    print("Aplikasi akan keluar.")
    sys.exit()

# Konfigurasi Global untuk simbol dan timeframe
symbol = "XAUUSD"

# Timeframe untuk berbagai strategi
AI_TRADING_TIMEFRAME = mt5.TIMEFRAME_M5
SCALPING_TIMEFRAME = mt5.TIMEFRAME_M1
SNIPER_TRADING_TIMEFRAME = mt5.TIMEFRAME_M1 # Timeframe utama untuk sniper entry
AI_HIGHER_TIMEFRAME = mt5.TIMEFRAME_H1
SCALPING_HIGHER_TIMEFRAME = mt5.TIMEFRAME_M5
SNIPER_HIGHER_TIMEFRAME = mt5.TIMEFRAME_M5 # Timeframe konfirmasi untuk sniper

model = None
is_running = False
current_mode = "Stopped"
win_count = 0
loss_count = 0

# Variabel Global Terkait Berita (Fundamental Analysis)
current_news_impact = "None" # Variabel ini hanya untuk tampilan UI, tidak untuk logika trading
news_event_time = None
news_effect_duration_minutes = 15
last_high_impact_news_time = None

# Variabel Global untuk UI dan Logika Tambahan
last_trade_result = "N/A" # "Win", "Loss", "N/A", "Gagal", "Gagal Tutup"
last_sniper_trade_time = None # Untuk cooldown sniper entry
SNIPER_COOLDOWN_SECONDS = 30 # Cooldown 30 detik setelah trade sniper

# Nama file untuk menyimpan pengaturan trading
# Menggunakan os.path.join untuk membuat jalur yang portabel dan eksplisit
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "trading_settings.json")


class TradingSettingsDialog(QDialog):
    """
    Dialog UI untuk mengatur parameter trading bot.
    Memungkinkan pengguna untuk mengubah ukuran lot, risiko, TP/SL, dll.
    """
    def __init__(self, parent=None, settings={}):
        """
        Inisialisasi dialog pengaturan.
        Args:
            parent: Parent widget (biasanya jendela utama bot).
            settings (dict): Kamus berisi pengaturan trading saat ini untuk ditampilkan sebagai default.
        """
        super().__init__(parent)
        self.setWindowTitle("⚙️ Pengaturan Trading")
        self.setGeometry(200, 200, 400, 450)
        
        self.layout = QGridLayout(self)

        self.lot_size_input = QDoubleSpinBox(); self.lot_size_input.setRange(0.01, 100.0); self.lot_size_input.setSingleStep(0.01)
        self.risk_percent_input = QDoubleSpinBox(); self.risk_percent_input.setRange(0.1, 10.0); self.risk_percent_input.setSingleStep(0.1)
        
        self.target_profit_usd_input = QDoubleSpinBox(); self.target_profit_usd_input.setRange(0.1, 1000.0); self.target_profit_usd_input.setSingleStep(0.1)
        self.target_loss_usd_input = QDoubleSpinBox(); self.target_loss_usd_input.setRange(1.0, 5000.0); self.target_loss_usd_input.setSingleStep(1.0)

        self.tp_pips_input = QDoubleSpinBox(); self.tp_pips_input.setRange(1, 1000); self.tp_pips_input.setSingleStep(1)
        self.sl_pips_input = QDoubleSpinBox(); self.sl_pips_input.setRange(1, 1000); self.sl_pips_input.setSingleStep(1)
        self.max_hold_duration_input = QDoubleSpinBox(); self.max_hold_duration_input.setRange(1, 120); self.max_hold_duration_input.setSingleStep(1)
        self.entry_method_combo = QComboBox(); self.entry_method_combo.addItems(["Instant", "Pending Order", "Stop Limit", "Market on Close"])
        self.retry_input = QDoubleSpinBox(); self.retry_input.setRange(0, 10); self.retry_input.setSingleStep(1)
        self.max_spread_input = QDoubleSpinBox(); self.max_spread_input.setRange(1, 200); self.max_spread_input.setSingleStep(1)
        
        self.min_tick_volume_scalping_input = QDoubleSpinBox(); self.min_tick_volume_scalping_input.setRange(0, 5000); self.min_tick_volume_scalping_input.setSingleStep(100); self.min_tick_volume_scalping_input.setValue(100)
        self.scalping_pattern_confidence_input = QDoubleSpinBox(); self.scalping_pattern_confidence_input.setRange(0.0, 1.0); self.scalping_pattern_confidence_input.setSingleStep(0.05); self.scalping_pattern_confidence_input.setValue(0.7)

        # Mengatur nilai awal input berdasarkan pengaturan yang diterima
        self.lot_size_input.setValue(settings.get('lot_size', 0.1))
        self.risk_percent_input.setValue(settings.get('risk_percent', 1.0))
        self.target_profit_usd_input.setValue(settings.get('target_profit_usd', 1.0))
        self.target_loss_usd_input.setValue(settings.get('target_loss_usd', 30.0))
        self.tp_pips_input.setValue(settings.get('tp_pips', 50))
        self.sl_pips_input.setValue(settings.get('sl_pips', 30))
        self.max_hold_duration_input.setValue(settings.get('max_hold_duration', 15))
        self.entry_method_combo.setCurrentText(settings.get('entry_method', "Instant"))
        self.retry_input.setValue(settings.get('max_retry', 3))
        self.max_spread_input.setValue(settings.get('max_spread', 50))
        self.min_tick_volume_scalping_input.setValue(settings.get('min_tick_volume_scalping', 100))
        self.scalping_pattern_confidence_input.setValue(settings.get('scalping_pattern_confidence', 0.7))

        # Menambahkan label dan input ke layout grid
        row = 0
        self.layout.addWidget(QLabel("Ukuran Lot:"), row, 0); self.layout.addWidget(self.lot_size_input, row, 1); row += 1
        self.layout.addWidget(QLabel("Risiko per Trade (%):"), row, 0); self.layout.addWidget(self.risk_percent_input, row, 1); row += 1
        self.layout.addWidget(QLabel("Target Profit ($):"), row, 0); self.layout.addWidget(self.target_profit_usd_input, row, 1); row += 1
        self.layout.addWidget(QLabel("Target Loss ($):"), row, 0); self.layout.addWidget(self.target_loss_usd_input, row, 1); row += 1
        self.layout.addWidget(QLabel("TP (Pips):"), row, 0); self.layout.addWidget(self.tp_pips_input, row, 1); row += 1
        self.layout.addWidget(QLabel("SL (Pips):"), row, 0); self.layout.addWidget(self.sl_pips_input, row, 1); row += 1
        self.layout.addWidget(QLabel("Max Hold (min):"), row, 0); self.layout.addWidget(self.max_hold_duration_input, row, 1); row += 1
        self.layout.addWidget(QLabel("Metode Entry:"), row, 0); self.layout.addWidget(self.entry_method_combo, row, 1); row += 1
        self.layout.addWidget(QLabel("Max Retry:"), row, 0); self.layout.addWidget(self.retry_input, row, 1); row += 1
        self.layout.addWidget(QLabel("Max Spread (points):"), row, 0); self.layout.addWidget(self.max_spread_input, row, 1); row += 1
        self.layout.addWidget(QLabel("Min Tick Volume (Scalping):"), row, 0); self.layout.addWidget(self.min_tick_volume_scalping_input, row, 1); row += 1
        self.layout.addWidget(QLabel("Conf. Pola (Scalping):"), row, 0); self.layout.addWidget(self.scalping_pattern_confidence_input, row, 1); row += 1

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        self.layout.addWidget(button_box, row, 0, 1, 2)
        self.setLayout(self.layout)

    def get_settings(self):
        """
        Mengambil semua nilai pengaturan dari input dialog dan mengembalikannya dalam bentuk kamus.
        Returns:
            dict: Kamus berisi pengaturan trading yang diatur oleh pengguna.
        """
        return {
            'lot_size': self.lot_size_input.value(),
            'risk_percent': self.risk_percent_input.value(),
            'target_profit_usd': self.target_profit_usd_input.value(),
            'target_loss_usd': self.target_loss_usd_input.value(),
            'tp_pips': self.tp_pips_input.value(),
            'sl_pips': self.sl_pips_input.value(),
            'max_hold_duration': self.max_hold_duration_input.value(),
            'entry_method': self.entry_method_combo.currentText(),
            'max_retry': self.retry_input.value(),
            'max_spread': self.max_spread_input.value(),
            'min_tick_volume_scalping': self.min_tick_volume_scalping_input.value(),
            'scalping_pattern_confidence': self.scalping_pattern_confidence_input.value()
        }

class TradingBotGUI(QWidget):
    """
    Kelas utama untuk GUI AI Trading Bot.
    Mengelola tampilan, interaksi pengguna, dan logika trading.
    """
    def __init__(self):
        """
        Inisialisasi jendela GUI utama bot.
        """
        super().__init__()
        self.setWindowTitle("🔥 AI TRADING BOT - XAUUSD REALTIME")
        self.resize(1000, 800)

        # Inisialisasi area output log terlebih dahulu
        # Ini penting agar self.log_output sudah ada saat load_settings() dipanggil
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("font-family: Consolas; font-size: 11px;")

        # Inisialisasi pengaturan trading dengan nilai default
        self.trading_settings = {
            'lot_size': 0.1,
            'risk_percent': 1.0,
            'target_profit_usd': 1.0,
            'target_loss_usd': 30.0,
            'tp_pips': 50,
            'sl_pips': 30,
            'max_hold_duration': 15,
            'entry_method': "Instant",
            'max_retry': 3,
            'max_spread': 50,
            'min_tick_volume_scalping': 100,
            'scalping_pattern_confidence': 0.7
        }
        self.load_settings() # Memuat pengaturan yang tersimpan saat inisialisasi

        self.setup_ui() # Membangun semua komponen UI
        
        self.data_timer = QTimer()
        self.data_timer.timeout.connect(self.update_market_data)
        self.data_timer.start(1000)
        
        self.analysis_timer = QTimer()
        self.analysis_timer.timeout.connect(self.run_analysis)
        
        self.news_timer = QTimer()
        self.news_timer.timeout.connect(self.check_economic_news)
        self.news_timer.start(30000)

        self.train_model()
        self.check_economic_news()

        self.set_mode("Monitoring")

    def save_settings(self):
        """
        Menyimpan pengaturan trading saat ini ke file JSON.
        """
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(self.trading_settings, f, indent=4)
            self.log("Pengaturan berhasil disimpan.")
            QMessageBox.information(self, "Pengaturan Tersimpan", "Pengaturan trading Anda telah berhasil disimpan!")
        except IOError as e:
            self.log(f"Error menyimpan pengaturan: {e}")
            QMessageBox.warning(self, "Error Menyimpan Pengaturan", f"Gagal menyimpan pengaturan: {e}\nPastikan Anda memiliki izin tulis di direktori:\n{os.path.dirname(SETTINGS_FILE)}")
        except Exception as e:
            self.log(f"Error tidak dikenal saat menyimpan pengaturan: {e}")
            QMessageBox.critical(self, "Error", f"Terjadi kesalahan tak terduga saat menyimpan pengaturan: {e}")


    def load_settings(self):
        """
        Memuat pengaturan trading dari file JSON.
        Jika file tidak ditemukan atau ada kesalahan, pengaturan default akan digunakan.
        """
        try:
            with open(SETTINGS_FILE, 'r') as f:
                loaded_settings = json.load(f)
                self.trading_settings.update(loaded_settings)
            self.log("Pengaturan berhasil dimuat.")
        except FileNotFoundError:
            self.log(f"File pengaturan '{SETTINGS_FILE}' tidak ditemukan. Menggunakan pengaturan default.")
        except json.JSONDecodeError as e:
            self.log(f"Error membaca file pengaturan (JSON tidak valid): {e}. Menggunakan pengaturan default.")
        except Exception as e:
            self.log(f"Error tidak dikenal saat memuat pengaturan: {e}. Menggunakan pengaturan default.")

    def setup_ui(self):
        """
        Membangun semua elemen UI (label, tombol, grup box, dll.)
        dan menempatkannya dalam tata letak.
        """
        self.layout = QVBoxLayout(self)
        
        header = QHBoxLayout()
        self.status_label = QLabel("🟢 BOT READY | Mode: Stopped")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.winrate_label = QLabel("Winrate: 0% (0/0)")
        self.winrate_label.setStyleSheet("font-size: 14px; font-weight: bold; color: blue;")
        header.addWidget(self.status_label, 70)
        header.addWidget(self.winrate_label, 30)
        self.layout.addLayout(header)

        analysis_box = QGroupBox("📊 Analisis Pasar (Teknikal)")
        analysis_layout = QGridLayout()
        
        self.price_label = QLabel("Harga: -")
        self.rsi_label = QLabel("RSI: -")
        self.macd_label = QLabel("MACD: -")
        self.ema_label = QLabel("EMA20/50: -")
        self.bb_label = QLabel("Lebar BB: -")
        self.atr_label = QLabel("ATR: -")
        self.trend_label = QLabel("Tren M5: -")
        self.obv_label = QLabel("Tren OBV: -")
        self.higher_tf_trend_label = QLabel("Tren H1: -")
        self.snr_label = QLabel("SNR: N/A")
        self.liquidity_label = QLabel("Likuiditas: N/A")
        self.overall_analysis_label = QLabel("Analisis Realtime: Menunggu...")
        self.last_trade_result_label = QLabel("Hasil Trade Terakhir: N/A") # Label baru
        self.last_trade_result_label.setStyleSheet("font-weight: bold; color: black;") # Default color
        
        # Apply initial styling to all labels for consistency
        for lbl in [self.price_label, self.rsi_label, self.macd_label,
                     self.ema_label, self.bb_label, self.atr_label, self.trend_label,
                     self.obv_label, self.higher_tf_trend_label, self.snr_label,
                     self.liquidity_label, self.overall_analysis_label, self.last_trade_result_label]:
            lbl.setStyleSheet("font-weight: bold;")
            
        # Row 0: Price and BB Width
        analysis_layout.addWidget(QLabel("🟢 Harga:"), 0, 0)
        analysis_layout.addWidget(self.price_label, 0, 1)
        analysis_layout.addWidget(QLabel("📌 Lebar BB:"), 0, 2)
        analysis_layout.addWidget(self.bb_label, 0, 3)

        # Row 1: RSI and ATR
        analysis_layout.addWidget(QLabel("📈 RSI:"), 1, 0)
        analysis_layout.addWidget(self.rsi_label, 1, 1)
        analysis_layout.addWidget(QLabel("📌 ATR:"), 1, 2)
        analysis_layout.addWidget(self.atr_label, 1, 3)

        # Row 2: MACD and M5 Trend
        analysis_layout.addWidget(QLabel("📊 MACD Hist:"), 2, 0) # Changed label to be more specific
        analysis_layout.addWidget(self.macd_label, 2, 1)
        analysis_layout.addWidget(QLabel("📌 Tren M5:"), 2, 2)
        analysis_layout.addWidget(self.trend_label, 2, 3)

        # Row 3: EMA and OBV Trend
        analysis_layout.addWidget(QLabel("📉 EMA20/50:"), 3, 0) # Changed label to be more specific
        analysis_layout.addWidget(self.ema_label, 3, 1)
        analysis_layout.addWidget(QLabel("⚖️ Tren OBV:"), 3, 2)
        analysis_layout.addWidget(self.obv_label, 3, 3)

        # Row 4: Higher TF Trend and SNR
        analysis_layout.addWidget(QLabel("⬆️ Tren H1:"), 4, 0)
        analysis_layout.addWidget(self.higher_tf_trend_label, 4, 1)
        analysis_layout.addWidget(QLabel("📍 SNR:"), 4, 2)
        analysis_layout.addWidget(self.snr_label, 4, 3)

        # Row 5: Liquidity and Overall Analysis
        analysis_layout.addWidget(QLabel("💧 Likuiditas:"), 5, 0)
        analysis_layout.addWidget(self.liquidity_label, 5, 1)
        analysis_layout.addWidget(QLabel("⚡ Analisis Realtime:"), 5, 2)
        analysis_layout.addWidget(self.overall_analysis_label, 5, 3)

        # Row 6: Last Trade Result (spanning across columns for visibility)
        analysis_layout.addWidget(QLabel("🏆 Hasil Trade Terakhir:"), 6, 0)
        analysis_layout.addWidget(self.last_trade_result_label, 6, 1, 1, 3) # Span across 3 columns
        
        analysis_box.setLayout(analysis_layout)
        self.layout.addWidget(analysis_box)

        news_box = QGroupBox("📰 Analisis Berita (Fundamental)")
        news_layout = QGridLayout()

        self.news_impact_label = QLabel("Dampak Saat Ini: None")
        self.news_impact_label.setStyleSheet("font-weight: bold;")
        self.next_news_label = QLabel("Berita Selanjutnya: N/A")
        self.next_news_label.setStyleSheet("font-weight: bold;")
        self.news_status_label = QLabel("Status: Mengecek...")
        self.news_status_label.setStyleSheet("font-weight: bold;")

        news_layout.addWidget(QLabel("⚡ Dampak Berita:"), 0, 0)
        news_layout.addWidget(self.news_impact_label, 0, 1)
        news_layout.addWidget(QLabel("📅 Berita Selanjutnya:"), 1, 0)
        news_layout.addWidget(self.next_news_label, 1, 1)
        news_layout.addWidget(QLabel("ℹ️ Status Berita:"), 2, 0)
        news_layout.addWidget(self.news_status_label, 2, 1)

        news_box.setLayout(news_layout)
        self.layout.addWidget(news_box)

        settings_box = QGroupBox("⚙️ Pengaturan Trading")
        settings_layout = QHBoxLayout()
        self.settings_button = QPushButton("Buka Pengaturan")
        self.settings_button.setStyleSheet("background-color: #607D8B; color: white; font-weight: bold;")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        settings_layout.addWidget(self.settings_button)
        settings_box.setLayout(settings_layout)
        self.layout.addWidget(settings_box)

        account_box = QGroupBox("💼 Info Akun")
        account_layout = QGridLayout()
        
        self.balance_label = QLabel("Balance: -")
        self.equity_label = QLabel("Equity: -")
        self.margin_label = QLabel("Margin: -")
        self.free_margin_label = QLabel("Free Margin: -")
        self.positions_label = QLabel("Open Positions: -")
        self.profit_label = QLabel("Current Profit: -")
        
        for lbl in [self.balance_label, self.equity_label, self.margin_label,
                     self.free_margin_label, self.positions_label, self.profit_label]:
            lbl.setStyleSheet("font-weight: bold;")
            
        account_layout.addWidget(QLabel("💰 Balance:"), 0, 0)
        account_layout.addWidget(self.balance_label, 0, 1)
        account_layout.addWidget(QLabel("📊 Equity:"), 1, 0)
        account_layout.addWidget(self.equity_label, 1, 1)
        account_layout.addWidget(QLabel("💳 Margin:"), 2, 0)
        account_layout.addWidget(self.margin_label, 2, 1)
        account_layout.addWidget(QLabel("🆓 Free Margin:"), 0, 2)
        account_layout.addWidget(self.free_margin_label, 0, 3)
        account_layout.addWidget(QLabel("📌 Positions:"), 1, 2)
        account_layout.addWidget(self.positions_label, 1, 3)
        account_layout.addWidget(QLabel("💰 Current Profit:"), 2, 2)
        account_layout.addWidget(self.profit_label, 2, 3)
        
        account_box.setLayout(account_layout)
        self.layout.addWidget(account_box)

        control_box = QGroupBox("⚙️ Control Panel")
        control_layout = QHBoxLayout()
        
        self.start_monitoring_button = QPushButton("👁️ Mulai Monitoring")
        self.start_monitoring_button.setStyleSheet("background-color: #607D8B; color: white; font-weight: bold;")
        self.start_monitoring_button.clicked.connect(self.toggle_monitoring_mode)

        self.start_ai_long_button = QPushButton("🚀 Mulai AI Long Trade")
        self.start_ai_long_button.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.start_ai_long_button.clicked.connect(self.toggle_ai_long_trade_mode)

        self.start_scalping_button = QPushButton("⚡ Mulai Scalping")
        self.start_scalping_button.setStyleSheet("background-color: #FFC107; color: black; font-weight: bold;")
        self.start_scalping_button.clicked.connect(self.toggle_scalping_mode)
        
        # Tombol baru untuk mode Sniper
        self.start_sniper_button = QPushButton("🎯 Mulai Sniper")
        self.start_sniper_button.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold;")
        self.start_sniper_button.clicked.connect(self.toggle_sniper_mode)

        self.train_button = QPushButton("🤖 Latih Model")
        self.train_button.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        self.train_button.clicked.connect(self.train_model)
        
        self.close_all_button = QPushButton("❌ Tutup Semua")
        self.close_all_button.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
        self.close_all_button.clicked.connect(self.close_all_positions)
        self.close_all_button.setToolTip("Tutup semua posisi yang terbuka segera")

        control_layout.addWidget(self.start_monitoring_button)
        control_layout.addWidget(self.start_ai_long_button)
        control_layout.addWidget(self.start_scalping_button)
        control_layout.addWidget(self.start_sniper_button) # Tambahkan tombol sniper
        control_layout.addWidget(self.train_button)
        control_layout.addWidget(self.close_all_button)
        control_box.setLayout(control_layout)
        self.layout.addWidget(control_box)

        # Menambahkan log_output ke layout
        self.layout.addWidget(self.log_output)

        self.update_market_data()
        self.update_account_info()
        self.setLayout(self.layout)

    def set_mode(self, mode):
        """
        Mengatur mode operasi bot dan memperbarui tampilan UI yang sesuai.
        Args:
            mode (str): Mode baru ('Stopped', 'Monitoring', 'AI_Long_Trade', 'Scalping_Bot', 'Sniper_Bot').
        """
        global current_mode, is_running
        
        self.analysis_timer.stop()
        
        self.start_monitoring_button.setText("👁️ Mulai Monitoring")
        self.start_monitoring_button.setStyleSheet("background-color: #607D8B; color: white; font-weight: bold;")
        self.start_ai_long_button.setText("🚀 Mulai AI Long Trade")
        self.start_ai_long_button.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.start_scalping_button.setText("⚡ Mulai Scalping")
        self.start_scalping_button.setStyleSheet("background-color: #FFC107; color: black; font-weight: bold;")
        self.start_sniper_button.setText("🎯 Mulai Sniper") # Reset sniper button
        self.start_sniper_button.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold;")

        current_mode = mode
        if mode == "Stopped":
            is_running = False
            self.status_label.setText("🔴 BOT STOPPED")
            self.log("Mode: Bot dihentikan.")

        elif mode == "Monitoring":
            is_running = True
            self.analysis_timer.start(5000)
            self.status_label.setText("🟢 BOT MONITORING")
            self.start_monitoring_button.setText("⛔ Hentikan Monitoring")
            self.start_monitoring_button.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
            self.log("Mode: Monitoring (Analisis Berita & Teknikal saja).")
            self.run_analysis()

        elif mode == "AI_Long_Trade":
            if model is None:
                self.log("Model belum dilatih! Harap latih model terlebih dahulu.")
                self.set_mode("Stopped")
                return
            is_running = True
            self.analysis_timer.start(60000)
            self.status_label.setText("🟢 BOT BERJALAN | Mode: AI Long Trade")
            self.start_ai_long_button.setText("⛔ Hentikan AI Long Trade")
            self.start_ai_long_button.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
            self.log("Mode: AI Long Trade diaktifkan. Analisis setiap 60 detik.")

        elif mode == "Scalping_Bot":
            is_running = True
            self.analysis_timer.start(5000)
            self.status_label.setText("🟢 BOT BERJALAN | Mode: Scalping")
            self.start_scalping_button.setText("⛔ Hentikan Scalping")
            self.start_scalping_button.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
            self.log("Mode: Scalping diaktifkan. Analisis setiap 5 detik.")
        
        elif mode == "Sniper_Bot": # Mode baru
            is_running = True
            self.analysis_timer.start(3000) # Cek lebih sering untuk sniper
            self.status_label.setText("🟢 BOT BERJALAN | Mode: Sniper")
            self.start_sniper_button.setText("⛔ Hentikan Sniper")
            self.start_sniper_button.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
            self.log("Mode: Sniper diaktifkan. Analisis setiap 3 detik.")
            
    def toggle_monitoring_mode(self):
        """Mengubah antara mode Monitoring dan Stopped."""
        if current_mode == "Monitoring":
            self.set_mode("Stopped")
        else:
            self.set_mode("Monitoring")

    def toggle_ai_long_trade_mode(self):
        """Mengubah antara mode AI Long Trade dan Stopped."""
        if current_mode == "AI_Long_Trade":
            self.set_mode("Stopped")
        else:
            self.set_mode("AI_Long_Trade")

    def toggle_scalping_mode(self):
        """Mengubah antara mode Scalping dan Stopped."""
        if current_mode == "Scalping_Bot":
            self.set_mode("Stopped")
        else:
            self.set_mode("Scalping_Bot")

    def toggle_sniper_mode(self): # Fungsi baru untuk toggle mode Sniper
        """Mengubah antara mode Sniper dan Stopped."""
        if current_mode == "Sniper_Bot":
            self.set_mode("Stopped")
        else:
            self.set_mode("Sniper_Bot")

    def open_settings_dialog(self):
        """
        Membuka dialog pengaturan trading dan menyimpan pengaturan jika diterima.
        """
        # Pastikan dialog dibuka dengan pengaturan yang sedang aktif
        dialog = TradingSettingsDialog(self, self.trading_settings)
        if dialog.exec() == QDialogButtonBox.StandardButton.Ok:
            self.trading_settings = dialog.get_settings()
            self.save_settings()

    def update_market_data(self):
        """
        Mengambil data pasar terbaru dari MT5, menghitung indikator teknikal,
        dan memperbarui label di UI. Fungsi ini berjalan setiap detik.
        """
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                self.log("Gagal mendapatkan data tick. Mencoba menyambung kembali ke MT5.")
                if not mt5.initialize():
                    self.log("FATAL: Gagal re-initialize MT5. Aplikasi mungkin tidak berfungsi.")
                return
                
            self.price_label.setText(f"{tick.ask:.2f}")
            
            rates_m5 = mt5.copy_rates_from_pos(symbol, AI_TRADING_TIMEFRAME, 0, 200)
            if rates_m5 is None:
                self.log("Gagal mendapatkan data candle M5 untuk display.")
                return
                
            df_m5 = pd.DataFrame(rates_m5)
            df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s')
            
            df_m5['rsi'] = RSIIndicator(df_m5['close'], window=14).rsi()
            macd = MACD(df_m5['close'])
            df_m5['macd'] = macd.macd()
            df_m5['macd_signal'] = macd.macd_signal()
            df_m5['macd_hist'] = macd.macd_diff()
            df_m5['ema20'] = EMAIndicator(df_m5['close'], window=20).ema_indicator()
            df_m5['ema50'] = EMAIndicator(df_m5['close'], window=50).ema_indicator()
            bb = BollingerBands(df_m5['close'], window=20, window_dev=2)
            df_m5['bb_upper'] = bb.bollinger_hband()
            df_m5['bb_lower'] = bb.bollinger_lband()
            df_m5['bb_middle'] = bb.bollinger_mavg()
            df_m5['bb_width'] = (df_m5['bb_upper'] - df_m5['bb_lower']) / df_m5['bb_middle']
            df_m5['atr'] = AverageTrueRange(df_m5['high'], df_m5['low'], df_m5['close'], window=14).average_true_range()
            
            if 'tick_volume' in df_m5.columns and not df_m5['tick_volume'].isnull().all():
                df_m5['obv'] = OnBalanceVolumeIndicator(df_m5['close'], df_m5['tick_volume']).on_balance_volume()
                if len(df_m5) > 10:
                    obv_sma = SMAIndicator(df_m5['obv'], window=10).sma_indicator()
                    current_obv = df_m5['obv'].iloc[-1]
                    current_obv_sma = obv_sma.iloc[-1]
                    
                    if current_obv > current_obv_sma:
                        self.obv_label.setText("Naik ▲")
                        self.obv_label.setStyleSheet("color: green; font-weight: bold;")
                    elif current_obv < current_obv_sma:
                        self.obv_label.setText("Turun ▼")
                        self.obv_label.setStyleSheet("color: red; font-weight: bold;")
                    else:
                        self.obv_label.setText("Datar ↔")
                        self.obv_label.setStyleSheet("color: gray; font-weight: bold;")
                else:
                    self.obv_label.setText("N/A (Data kurang)")
                    self.obv_label.setStyleSheet("color: gray; font-weight: bold;")
            else:
                self.log("Peringatan: 'tick_volume' tidak ditemukan di data M5 untuk OBV. Pastikan MT5 menyediakan volume.")
                df_m5['obv'] = np.nan
                self.obv_label.setText("N/A (No Volume)")
                self.obv_label.setStyleSheet("color: gray; font-weight: bold;")

            last_m5 = df_m5.iloc[-1]
            
            # Enhanced RSI color logic
            rsi_val = last_m5['rsi']
            self.rsi_label.setText(f"{rsi_val:.2f}")
            if rsi_val < 30:
                self.rsi_label.setStyleSheet("color: blue; font-weight: bold;") # Oversold
            elif rsi_val > 70:
                self.rsi_label.setStyleSheet("color: red; font-weight: bold;") # Overbought
            else:
                self.rsi_label.setStyleSheet("color: green; font-weight: bold;") # Neutral

            # Enhanced MACD Histogram color logic
            macd_hist_val = last_m5['macd_hist']
            self.macd_label.setText(f"{macd_hist_val:.4f}")
            if macd_hist_val > 0:
                self.macd_label.setStyleSheet("color: green; font-weight: bold;")
            elif macd_hist_val < 0:
                self.macd_label.setStyleSheet("color: red; font-weight: bold;")
            else:
                self.macd_label.setStyleSheet("color: gray; font-weight: bold;")

            self.ema_label.setText(f"{last_m5['ema20']:.2f}/{last_m5['ema50']:.2f}")
            self.bb_label.setText(f"{last_m5['bb_width']:.4f}")
            self.atr_label.setText(f"{last_m5['atr']:.2f}")
            
            if last_m5['close'] > last_m5['ema20'] and last_m5['ema20'] > last_m5['ema50']:
                self.trend_label.setText("Naik Kuat ▲▲")
                self.trend_label.setStyleSheet("color: green; font-weight: bold;")
            elif last_m5['close'] > last_m5['ema20']:
                self.trend_label.setText("Naik ▲")
                self.trend_label.setStyleSheet("color: darkgreen; font-weight: bold;")
            elif last_m5['close'] < last_m5['ema20'] and last_m5['ema20'] < last_m5['ema50']:
                self.trend_label.setText("Turun Kuat ▼▼")
                self.trend_label.setStyleSheet("color: red; font-weight: bold;")
            elif last_m5['close'] < last_m5['ema20']:
                self.trend_label.setText("Turun ▼")
                self.trend_label.setStyleSheet("color: darkred; font-weight: bold;")
            else:
                self.trend_label.setText("Sideways ↔")
                self.trend_label.setStyleSheet("color: gray; font-weight: bold;")

            higher_tf_for_display = AI_HIGHER_TIMEFRAME
            if current_mode == "AI_Long_Trade":
                higher_tf_for_display = AI_HIGHER_TIMEFRAME
            elif current_mode == "Scalping_Bot":
                higher_tf_for_display = SCALPING_HIGHER_TIMEFRAME
            elif current_mode == "Sniper_Bot": # Gunakan timeframe yang sesuai untuk sniper
                higher_tf_for_display = SNIPER_HIGHER_TIMEFRAME

            rates_higher_tf = mt5.copy_rates_from_pos(symbol, higher_tf_for_display, 0, 50)
            if rates_higher_tf is None:
                self.log(f"Gagal mendapatkan data candle untuk {higher_tf_for_display}.")
                self.higher_tf_trend_label.setText("N/A")
                return
                
            df_higher_tf = pd.DataFrame(rates_higher_tf)
            df_higher_tf['time'] = pd.to_datetime(df_higher_tf['time'], unit='s')
            
            df_higher_tf['sma20'] = SMAIndicator(df_higher_tf['close'], window=20).sma_indicator()
            df_higher_tf['sma50'] = SMAIndicator(df_higher_tf['close'], window=50).sma_indicator()
            df_higher_tf = df_higher_tf.dropna()

            if not df_higher_tf.empty:
                last_higher_tf = df_higher_tf.iloc[-1]
                if last_higher_tf['sma20'] > last_higher_tf['sma50']:
                    self.higher_tf_trend_label.setText("Up Trend ▲")
                    self.higher_tf_trend_label.setStyleSheet("color: green; font-weight: bold;")
                elif last_higher_tf['sma20'] < last_higher_tf['sma50']:
                    self.higher_tf_trend_label.setText("Down Trend ▼")
                    self.higher_tf_trend_label.setStyleSheet("color: red; font-weight: bold;")
                else:
                    self.higher_tf_trend_label.setText("Sideways ↔")
                    self.higher_tf_trend_label.setStyleSheet("color: gray; font-weight: bold;")
            else:
                self.higher_tf_trend_label.setText("N/A")
                self.higher_tf_trend_label.setStyleSheet("color: gray; font-weight: bold;")
            
            if len(df_m5) >= 20:
                recent_high = df_m5['high'].iloc[-20:].max()
                recent_low = df_m5['low'].iloc[-20:].min()
                current_close = df_m5['close'].iloc[-1]
                
                distance_to_high = abs(current_close - recent_high)
                distance_to_low = abs(current_close - recent_low)
                
                current_atr = df_m5['atr'].iloc[-1] if not df_m5['atr'].isnull().iloc[-1] else 0.5
                
                snr_status = "N/A"
                if current_atr > 0:
                    if distance_to_high < (0.5 * current_atr) and current_close < recent_high:
                        snr_status = f"Dekat R: {recent_high:.2f}"
                        self.snr_label.setStyleSheet("color: orange; font-weight: bold;")
                    elif distance_to_low < (0.5 * current_atr) and current_close > recent_low:
                        snr_status = f"Dekat S: {recent_low:.2f}"
                        self.snr_label.setStyleSheet("color: blue; font-weight: bold;")
                    else:
                        snr_status = "Antara S/R"
                        self.snr_label.setStyleSheet("color: black; font-weight: bold;")
                self.snr_label.setText(snr_status)
            else:
                self.snr_label.setText("N/A (Data kurang)")
                self.snr_label.setStyleSheet("color: gray; font-weight: bold;")

            current_spread_points = (tick.ask - tick.bid) / mt5.symbol_info(symbol).point
            avg_tick_volume_m5 = df_m5['tick_volume'].mean() if 'tick_volume' in df_m5.columns and not df_m5['tick_volume'].isnull().all() else 0

            liquidity_status = "N/A"
            if current_spread_points <= self.trading_settings['max_spread'] * 0.5 and avg_tick_volume_m5 > self.trading_settings['min_tick_volume_scalping'] * 5:
                liquidity_status = "Sangat Baik"
                self.liquidity_label.setStyleSheet("color: green; font-weight: bold;")
            elif current_spread_points <= self.trading_settings['max_spread'] and avg_tick_volume_m5 > self.trading_settings['min_tick_volume_scalping']:
                liquidity_status = "Baik"
                self.liquidity_label.setStyleSheet("color: darkgreen; font-weight: bold;")
            else:
                liquidity_status = "Rendah"
                self.liquidity_label.setStyleSheet("color: red; font-weight: bold;")
            self.liquidity_label.setText(liquidity_status)

            self.update_account_info()
            self.update_overall_analysis()

        except Exception as e:
            self.log(f"Error memperbarui data pasar: {str(e)}")

    def update_account_info(self):
        """
        Mengambil informasi akun dari MT5 dan memperbarui label di UI.
        """
        try:
            account = mt5.account_info()
            if account:
                self.balance_label.setText(f"${account.balance:.2f}")
                self.equity_label.setText(f"${account.equity:.2f}")
                self.margin_label.setText(f"${account.margin:.2f}")
                self.free_margin_label.setText(f"${account.margin_free:.2f}")
                
                positions = mt5.positions_get(symbol=symbol)
                if positions is None or len(positions) == 0:
                    self.positions_label.setText("0")
                    self.profit_label.setText("$0.00")
                    self.profit_label.setStyleSheet("color: black; font-weight: bold;")
                else:
                    self.positions_label.setText(f"{len(positions)}")
                    
                    total_profit = 0.0
                    for pos in positions:
                        total_profit += pos.profit
                                            
                    self.profit_label.setText(f"${total_profit:.2f}")
                    
                    if total_profit > 0:
                        self.profit_label.setStyleSheet("color: green; font-weight: bold;")
                        self.equity_label.setStyleSheet("color: green; font-weight: bold;")
                    elif total_profit < 0:
                        self.profit_label.setStyleSheet("color: red; font-weight: bold;")
                        self.equity_label.setStyleSheet("color: red; font-weight: bold;")
                    else:
                        self.profit_label.setStyleSheet("color: black; font-weight: bold;")
                        self.equity_label.setStyleSheet("color: black; font-weight: bold;")
                    
        except Exception as e:
            self.log(f"Error memperbarui info akun: {str(e)}")

    def update_overall_analysis(self):
        """
        Menentukan dan menampilkan analisis keseluruhan chart (naik/turun/sideways/volatil)
        berdasarkan analisis teknikal (tren). Berita hanya untuk informasi, bukan logika trading.
        """
        m5_trend = self.trend_label.text().split(' ')[0]
        h1_trend = self.higher_tf_trend_label.text().split(' ')[0]
        # news_impact = current_news_impact # Dihapus: news_impact tidak lagi digunakan untuk logika trading
        
        overall_status = "Tidak Yakin ↔"
        status_color = "gray"

        # Logika analisis keseluruhan sekarang hanya berdasarkan indikator teknikal
        if ("Naik" in m5_trend or "Up" in m5_trend) and ("Up" in h1_trend):
            overall_status = "Potensi Naik Kuat ⬆️⬆️"
            status_color = "green"
        elif ("Turun" in m5_trend or "Down" in m5_trend) and ("Down" in h1_trend):
            overall_status = "Potensi Turun Kuat ⬇️⬇️"
            status_color = "red"
        elif ("Naik" in m5_trend or "Up" in h1_trend):
            overall_status = "Potensi Naik ⬆️"
            status_color = "darkgreen"
        elif ("Turun" in m5_trend or "Down" in h1_trend):
            overall_status = "Potensi Turun ⬇️"
            status_color = "darkred"
        else:
            overall_status = "Sideways/Konsolidasi ↔"
            status_color = "blue"

        self.overall_analysis_label.setText(overall_status)
        self.overall_analysis_label.setStyleSheet(f"font-weight: bold; color: {status_color};")


    def train_model(self):
        """
        Melatih model RandomForestClassifier menggunakan data historis M5.
        Model ini digunakan untuk strategi AI Long Trade.
        """
        global model
        self.log("Memulai pelatihan model...")
        
        try:
            rates = mt5.copy_rates_from_pos(symbol, AI_TRADING_TIMEFRAME, 0, 2000)
            if rates is None:
                self.log("Gagal mendapatkan data historis M5 untuk pelatihan model.")
                return
                
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            
            df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
            macd = MACD(df['close'])
            df['macd'] = macd.macd()
            df['macd_signal'] = macd.macd_signal()
            df['macd_hist'] = macd.macd_diff()
            df['ema20'] = EMAIndicator(df['close'], window=20).ema_indicator()
            df['ema50'] = EMAIndicator(df['close'], window=50).ema_indicator()
            bb = BollingerBands(df['close'], window=20, window_dev=2)
            df['bb_upper'] = bb.bollinger_hband()
            df['bb_lower'] = bb.bollinger_lband()
            df['bb_middle'] = bb.bollinger_mavg()
            df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
            df['atr'] = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
            
            if 'tick_volume' in df.columns and not df['tick_volume'].isnull().all():
                df['obv'] = OnBalanceVolumeIndicator(df['close'], df['tick_volume']).on_balance_volume()
            else:
                self.log("Peringatan: 'tick_volume' tidak ditemukan di data M5 untuk OBV saat training. Menggunakan nilai nol untuk OBV.")
                df['obv'] = 0

            df['target'] = (df['close'].shift(-1) > df['close']).astype(int)
            df = df.dropna()
            
            if df.empty:
                self.log("Data tidak cukup setelah perhitungan indikator untuk melatih model.")
                return

            X = df[['open', 'high', 'low', 'close', 'rsi', 'macd', 'macd_signal',
                     'macd_hist', 'ema20', 'ema50', 'bb_width', 'atr', 'obv']]
            y = df['target']
            
            if len(X) < 2:
                self.log("Data terlalu sedikit untuk melakukan train-test split. Tingkatkan jumlah data historis.")
                return

            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
            
            if X_train.empty or X_test.empty:
                self.log("Train atau test set kosong setelah split. Sesuaikan ukuran data historis atau test_size.")
                return

            model = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42)
            model.fit(X_train, y_train)
            
            train_score = model.score(X_train, y_train)
            test_score = model.score(X_test, y_test)
            
            self.log(f"Pelatihan model selesai. Akurasi: Train={train_score:.2f}, Test={test_score:.2f}")
            self.status_label.setText("🟢 BOT READY | Model dilatih")
            
        except Exception as e:
            self.log(f"Error melatih model: {str(e)}")
            self.status_label.setText("🔴 BOT ERROR | Pelatihan gagal")

    def close_all_positions(self):
        """
        Menutup semua posisi trading yang terbuka untuk simbol yang sedang diperdagangkan.
        """
        try:
            positions = mt5.positions_get(symbol=symbol)
            if positions is None or len(positions) == 0:
                self.log("Tidak ada posisi terbuka untuk ditutup.")
                return
                
            self.log(f"Mencoba menutup {len(positions)} posisi...")
            
            for position in positions:
                self.close_position(position)
                
            self.update_account_info()
            self.update_winrate()
            
        except Exception as e:
            self.log(f"Error closing positions: {str(e)}")

    def update_winrate(self):
        """
        Memperbarui dan menampilkan persentase kemenangan di UI.
        """
        total = win_count + loss_count
        if total > 0:
            winrate = (win_count / total) * 100
            self.winrate_label.setText(f"Winrate: {winrate:.1f}% ({win_count}/{total})")
            if winrate >= 50:
                self.winrate_label.setStyleSheet("color: green; font-weight: bold;")
            else:
                self.winrate_label.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.winrate_label.setText("Winrate: 0% (0/0)")
            self.winrate_label.setStyleSheet("color: blue; font-weight: bold;")


    def calculate_lot_size_by_risk(self, risk_amount_usd, sl_pips_for_trade, current_price):
        """
        Menghitung ukuran lot yang tepat berdasarkan jumlah uang yang bersedia dirisikokan
        dan Stop Loss dalam pips. Ini memastikan manajemen risiko yang konsisten.
        Args:
            risk_amount_usd (float): Jumlah maksimum USD yang bersedia dirisikokan per trade.
            sl_pips_for_trade (float): Jarak Stop Loss dalam pips untuk trade ini.
            current_price (float): Harga masuk pasar saat ini (digunakan untuk mendapatkan info simbol).
        Returns:
            float: Ukuran lot yang dihitung, dibulatkan ke volume step yang valid.
        """
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            self.log(f"Gagal mendapatkan info simbol untuk {symbol} di calculate_lot_size_by_risk.")
            return 0.0

        cost_per_pip_per_lot = 10.0 # Standard $10 per pip per lot untuk XAUUSD

        if sl_pips_for_trade <= 0:
            self.log("ERROR: SL Pips untuk perhitungan lot harus lebih dari nol.")
            return 0.0

        risk_per_lot_at_sl = sl_pips_for_trade * cost_per_pip_per_lot

        if risk_per_lot_at_sl == 0:
            self.log("ERROR: Risiko per lot di SL adalah nol. Tidak dapat menghitung lot.")
            return 0.0

        calculated_lot_size = risk_amount_usd / risk_per_lot_at_sl

        min_lot = symbol_info.volume_min
        max_lot = symbol_info.volume_max
        step_lot = symbol_info.volume_step

        calculated_lot_size = max(min_lot, min(calculated_lot_size, max_lot))
        calculated_lot_size = round(calculated_lot_size / step_lot) * step_lot
        
        self.log(f"Perhitungan Lot: Risiko=${risk_amount_usd:.2f}, SL={sl_pips_for_trade} pips, Lot Dihitung={calculated_lot_size:.2f}")

        return calculated_lot_size

    def execute_trade(self, signal, price, df, lot_size_override=None, tp_pips_override=None, sl_pips_override=None):
        """
        Mengeksekusi order trading (BUY/SELL) dengan parameter yang ditentukan.
        Ini adalah fungsi inti untuk membuka posisi.
        Args:
            signal (int): 1 untuk BUY, 0 untuk SELL.
            price (float): Harga eksekusi order (ask untuk BUY, bid untuk SELL).
            df (DataFrame): DataFrame data candle terbaru untuk logging indikator.
            lot_size_override (float, optional): Ukuran lot yang akan digunakan, jika tidak, pakai dari pengaturan.
            tp_pips_override (float, optional): TP dalam pips, jika tidak, pakai dari pengaturan.
            sl_pips_override (float, optional): SL dalam pips, jika tidak, pakai dari pengaturan.
        Returns:
            mt5.TradeRequestResult or None: Hasil dari operasi order_send MT5.
        """
        try:
            lot_size = lot_size_override if lot_size_override is not None else self.trading_settings['lot_size']
            tp_pips = tp_pips_override if tp_pips_override is not None else self.trading_settings['tp_pips']
            sl_pips = sl_pips_override if sl_pips_override is not None else self.trading_settings['sl_pips']
            
            entry_method = self.trading_settings['entry_method']
            max_retry = self.trading_settings['max_retry']
            
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                self.log(f"Gagal mendapatkan info simbol untuk {symbol}")
                return None
                
            point = symbol_info.point
            pip_value = point * 10 # 1 pip = 10 points for XAUUSD typically
            
            if signal == 1: # BUY
                take_profit_price = price + tp_pips * pip_value
                stop_loss_price = price - sl_pips * pip_value
                order_type = mt5.ORDER_TYPE_BUY
            else: # SELL
                take_profit_price = price - tp_pips * pip_value
                stop_loss_price = price + sl_pips * pip_value
                order_type = mt5.ORDER_TYPE_SELL
            
            last_row = df.iloc[-1]
            self.log(f"📊 INDICATORS for Trade:")
            self.log(f"    Harga: {last_row['close']:.2f}, RSI: {last_row['rsi']:.2f}, MACD Hist: {last_row['macd_hist']:.4f}")
            self.log(f"    EMA: {last_row['ema20']:.2f}/{last_row['ema50']:.2f}, BB Width: {last_row['bb_width']:.4f}, ATR: {last_row['atr']:.2f}")
            self.log(f"    OBV: {last_row.get('obv', 'N/A')}")
            
            if entry_method == "Instant":
                result = self.execute_instant_order(order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry)
            elif entry_method == "Pending Order":
                result = self.execute_pending_order(order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry)
            elif entry_method == "Stop Limit":
                result = self.execute_stop_limit_order(order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry)
            elif entry_method == "Market on Close":
                result = self.execute_market_on_close(order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry)
                
            return result
                
        except Exception as e:
            self.log(f"Error dalam eksekusi trade: {str(e)}")
            return None

    def execute_instant_order(self, order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry=3):
        """
        Mengeksekusi order instan (Market Execution).
        Melakukan pengecekan spread, margin, dan mencoba kembali jika terjadi requote.
        Args:
            order_type (int): Tipe order MT5 (mt5.ORDER_TYPE_BUY atau mt5.ORDER_TYPE_SELL).
            price (float): Harga eksekusi yang diinginkan.
            lot_size (float): Ukuran lot.
            take_profit_price (float): Harga Take Profit.
            stop_loss_price (float): Harga Stop Loss.
            max_retry (int): Jumlah maksimum percobaan jika order gagal karena requote/perubahan harga.
        Returns:
            mt5.TradeRequestResult or None: Hasil dari operasi order_send.
        """
        account = mt5.account_info()
        if account is None:
            self.log("❌ Gagal mendapatkan info akun.")
            return None
            
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            self.log(f"❌ Gagal mendapatkan info simbol untuk {symbol}.")
            return None
            
        point = symbol_info.point
        
        if not symbol_info.visible or not symbol_info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL:
            self.log("❌ Simbol tidak tersedia untuk trading.")
            return None
            
        spread = symbol_info.ask - symbol_info.bid
        current_spread_points = spread / point
        if current_spread_points > self.trading_settings['max_spread']:
            self.log(f"❌ Spread terlalu lebar: {current_spread_points:.1f} poin (maks {self.trading_settings['max_spread']:.1f}).")
            return None
            
        if order_type == mt5.ORDER_TYPE_BUY:
            required_margin = mt5.order_calc_margin(
                mt5.ORDER_TYPE_BUY, symbol, lot_size, symbol_info.ask
            )
        else:
            required_margin = mt5.order_calc_margin(
                mt5.ORDER_TYPE_SELL, symbol, lot_size, symbol_info.bid
            )
            
        if required_margin is None:
            self.log("❌ Gagal menghitung margin yang dibutuhkan.")
            return None
            
        if account.margin_free < required_margin:
            self.log(f"❌ Margin tidak cukup. Dibutuhkan: ${required_margin:.2f}, Tersedia: ${account.margin_free:.2f}.")
            return None
            
        for attempt in range(max_retry + 1):
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                self.log("Gagal mendapatkan tick terbaru saat retry untuk instant order.")
                time.sleep(0.5)
                continue

            current_price_for_order = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
                
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_size,
                "type": order_type,
                "price": current_price_for_order,
                "sl": stop_loss_price,
                "tp": take_profit_price,
                "deviation": 10,
                "magic": 123456,
                "comment": f"AI Instant Entry (Attempt {attempt+1})",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }
            
            result = mt5.order_send(request)
            
            if result is None:
                self.log(f"❌ Hasil order_send (Instant) adalah None. Kemungkinan masalah koneksi atau server. Percobaan {attempt+1}/{max_retry}")
                if attempt < max_retry:
                    time.sleep(1)
                    continue
                else:
                    return None

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                self.handle_order_result(result, "Instant", attempt+1)
                return result
            elif result.retcode in [mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_PRICE_CHANGED]:
                self.log(f"🔄 Harga berubah. Mencoba lagi (Percobaan {attempt+1}/{max_retry})...")
                time.sleep(0.5)
                continue
            else:
                self.handle_order_result(result, "Instant", attempt+1)
                return result
                
        return result

    def execute_pending_order(self, order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry=3):
        """
        Mengeksekusi order pending (Limit Order).
        Akan ditempatkan di harga tertentu, di bawah harga saat ini untuk BUY LIMIT,
        atau di atas harga saat ini untuk SELL LIMIT.
        Args:
            order_type (int): Tipe order MT5 (mt5.ORDER_TYPE_BUY atau mt5.ORDER_TYPE_SELL).
            price (float): Harga saat ini (digunakan untuk menentukan harga limit).
            lot_size (float): Ukuran lot.
            take_profit_price (float): Harga Take Profit.
            stop_loss_price (float): Harga Stop Loss.
            max_retry (int): Jumlah maksimum percobaan.
        Returns:
            mt5.TradeRequestResult or None: Hasil dari operasi order_send.
        """
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            self.log(f"Gagal mendapatkan info simbol untuk {symbol} saat pending order.")
            return None
        point = symbol_info.point

        if order_type == mt5.ORDER_TYPE_BUY:
            pending_type = mt5.ORDER_TYPE_BUY_LIMIT
            entry_price = price - (10 * point) # Example: 1 pip below current price
        else:
            pending_type = mt5.ORDER_TYPE_SELL_LIMIT
            entry_price = price + (10 * point) # Example: 1 pip above current price
            
        for attempt in range(max_retry + 1):
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": lot_size,
                "type": pending_type,
                "price": entry_price,
                "sl": stop_loss_price,
                "tp": take_profit_price,
                "deviation": 10,
                "magic": 123456,
                "comment": f"AI Pending Entry (Attempt {attempt+1})",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC, # Immediate or Cancel
            }
            
            result = mt5.order_send(request)
            
            if result is None:
                self.log(f"❌ Hasil order_send (Pending) adalah None. Kemungkinan masalah koneksi atau server. Percobaan {attempt+1}/{max_retry}")
                if attempt < max_retry:
                    time.sleep(1)
                    continue
                else:
                    return None
            
            if result.retcode == mt5.TRADE_RETCODE_DONE or attempt == max_retry:
                self.handle_order_result(result, "Pending", attempt+1)
                return result
                
            time.sleep(1)
            self.log(f"🔄 Retry {attempt+1}/{max_retry} for Pending Order...")
            
        return result

    def execute_stop_limit_order(self, order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry=3):
        """
        Mengeksekusi order Stop Limit.
        Order Stop Limit membutuhkan dua harga: harga stop (trigger) dan harga limit (eksekusi).
        Args:
            order_type (int): Tipe order MT5 (mt5.ORDER_TYPE_BUY atau mt5.ORDER_TYPE_SELL).
            price (float): Harga saat ini.
            lot_size (float): Ukuran lot.
            take_profit_price (float): Harga Take Profit.
            stop_loss_price (float): Harga Stop Loss.
            max_retry (int): Jumlah maksimum percobaan.
        Returns:
            mt5.TradeRequestResult or None: Hasil dari operasi order_send.
        """
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            self.log(f"Gagal mendapatkan info simbol untuk {symbol} saat stop limit order.")
            return None
        point = symbol_info.point

        if order_type == mt5.ORDER_TYPE_BUY:
            stop_limit_type = mt5.ORDER_TYPE_BUY_STOP_LIMIT
            stop_price = price + (10 * point)  # Price at which stop limit becomes active
            limit_price = price + (5 * point)   # Limit price for the buy
        else:
            stop_limit_type = mt5.ORDER_TYPE_SELL_STOP_LIMIT
            stop_price = price - (10 * point)  # Price at which stop limit becomes active
            limit_price = price - (5 * point)   # Limit price for the sell
            
        for attempt in range(max_retry + 1):
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": lot_size,
                "type": stop_limit_type,
                "price": limit_price,
                "stoplimit": stop_price, # This is the trigger price for STOP_LIMIT orders
                "sl": stop_loss_price,
                "tp": take_profit_price,
                "deviation": 10,
                "magic": 123456,
                "comment": f"AI Stop Limit Entry (Attempt {attempt+1})",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            
            if result is None:
                self.log(f"❌ Hasil order_send (Stop Limit) adalah None. Kemungkinan masalah koneksi atau server. Percobaan {attempt+1}/{max_retry}")
                if attempt < max_retry:
                    time.sleep(1)
                    continue
                else:
                    return None

            if result.retcode == mt5.TRADE_RETCODE_DONE or attempt == max_retry:
                self.handle_order_result(result, "Stop Limit", attempt+1)
                return result
                
            time.sleep(1)
            self.log(f"🔄 Retry {attempt+1}/{max_retry} for Stop Limit Order...")
                
        return result

    def execute_market_on_close(self, order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry=3):
        """
        Mengeksekusi order "Market on Close".
        Jika waktu saat ini sangat dekat dengan penutupan candle, order akan dieksekusi sebagai order instan.
        Jika tidak, order pending akan ditempatkan dan akan kadaluarsa pada penutupan candle.
        Args:
            order_type (int): Tipe order MT5 (mt5.ORDER_TYPE_BUY atau mt5.ORDER_TYPE_SELL).
            price (float): Harga saat ini.
            lot_size (float): Ukuran lot.
            take_profit_price (float): Harga Take Profit.
            stop_loss_price (float): Harga Stop Loss.
            max_retry (int): Jumlah maksimum percobaan.
        Returns:
            mt5.TradeRequestResult or None: Hasil dari operasi order.
        """
        rates = mt5.copy_rates_from_pos(symbol, AI_TRADING_TIMEFRAME, 0, 1)
        if rates is None:
            self.log("Gagal mendapatkan data candle untuk Market on Close.")
            return None
            
        candle_time = pd.to_datetime(rates[0]['time'], unit='s')
        current_time = datetime.datetime.now()
        time_diff = (current_time - candle_time).total_seconds()
        
        # If very close to candle close, execute as instant order
        if time_diff > (mt5.period_seconds(AI_TRADING_TIMEFRAME) - 10):
            self.log("Melakukan Market on Close sebagai Instant Order (dekat penutupan candle).")
            return self.execute_instant_order(order_type, price, lot_size, take_profit_price, stop_loss_price, max_retry)
        else:
            # Place a pending order expiring at the next candle close
            expiration = candle_time + datetime.timedelta(seconds=mt5.period_seconds(AI_TRADING_TIMEFRAME))
            
            for attempt in range(max_retry + 1):
                request = {
                    "action": mt5.TRADE_ACTION_PENDING,
                    "symbol": symbol,
                    "volume": lot_size,
                    "type": mt5.ORDER_TYPE_BUY_LIMIT if order_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_SELL_LIMIT,
                    "price": price,
                    "sl": stop_loss_price,
                    "tp": take_profit_price,
                    "deviation": 10,
                    "magic": 123456,
                    "comment": f"AI Market on Close (Attempt {attempt+1})",
                    "type_time": mt5.ORDER_TIME_SPECIFIED,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                    "expiration": int(expiration.timestamp())
                }
                
                result = mt5.order_send(request)
                
                if result is None:
                    self.log(f"❌ Hasil order_send (MOC) adalah None. Kemungkinan masalah koneksi atau server. Percobaan {attempt+1}/{max_retry}")
                    if attempt < max_retry:
                        time.sleep(1)
                        continue
                    else:
                        return None

                if result.retcode == mt5.TRADE_RETCODE_DONE or attempt == max_retry:
                    self.handle_order_result(result, "Market on Close", attempt+1)
                    return result
                        
                time.sleep(1)
                self.log(f"🔄 Retry {attempt+1}/{max_retry} for Market on Close...")
                        
            return result

    def handle_order_result(self, result, order_type, attempt=1):
        """
        Memproses hasil dari operasi order_send dan mencatatnya ke log.
        Args:
            result (mt5.TradeRequestResult): Objek hasil dari order_send.
            order_type (str): Tipe order (misalnya, "Instant", "Pending").
            attempt (int): Nomor percobaan order.
        """
        global win_count, loss_count, last_trade_result
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            error_msg = self.get_error_message(result.retcode)
            self.log(f"❌ Gagal mengeksekusi order {order_type} (Percobaan {attempt}): {error_msg}. Retcode: {result.retcode}")
            last_trade_result = "Gagal"
        else:
            self.log(f"🎯 {order_type} {'BELI' if result.request.type==mt5.ORDER_TYPE_BUY else 'JUAL'} @ {result.price:.2f}")
            self.log(f"    TP: {result.request.tp:.2f} | SL: {result.request.sl:.2f} | Lot: {result.request.volume:.2f}")
            last_trade_result = "Berhasil"
            
        self.update_account_info()
        self.update_last_trade_result_label() # Perbarui label hasil trade

    def check_economic_news(self):
        """
        Mengecek berita ekonomi yang disimulasikan dan memperbarui status dampak berita.
        Fungsi ini mensimulasikan pengambilan data berita ekonomi, dalam aplikasi nyata
        ini akan memanggil API berita eksternal.
        """
        global current_news_impact, news_event_time, last_high_impact_news_time
        
        self.news_status_label.setText("Mengecek berita...")
        self.news_status_label.setStyleSheet("font-weight: bold; color: gray;")

        # Simpan dampak berita aktual untuk tujuan tampilan UI saja
        display_news_impact = "None"
        display_news_impact_color = "green"

        try:
            news_data = self._simulate_economic_news()
            
            next_event_time = None
            next_event_details = "N/A"
            
            current_time_wib = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))
            
            news_data.sort(key=lambda x: datetime.datetime.strptime(x['time'], "%Y-%m-%d %H:%M:%S"))

            for news_item in news_data:
                news_time = datetime.datetime.strptime(news_item['time'], "%Y-%m-%d %H:%M:%S")

                if news_time > current_time_wib.replace(tzinfo=None):
                    # Ditemukan event berita di masa depan, simpan yang paling awal
                    if next_event_time is None or news_time < next_event_time:
                        next_event_time = news_time
                        next_event_details = f"{news_item['headline']} ({news_item['impact']}) pada {news_time.strftime('%H:%M')}"
                        self.next_news_label.setStyleSheet("font-weight: bold; color: orange;")

                # Cek dampak berita yang aktif HANYA UNTUK TAMPILAN
                if current_time_wib.replace(tzinfo=None) >= news_time and \
                   (current_time_wib.replace(tzinfo=None) - news_time).total_seconds() / 60 < news_effect_duration_minutes:
                    if news_item['impact'] == 'High':
                        display_news_impact = "TINGGI"
                        display_news_impact_color = "red"
                        self.log(f"🔥 BERITA BERDAMPAK TINGGI: {news_item['headline']} pada {news_time.strftime('%H:%M:%S')}")
                        break # Dampak tinggi menimpa yang lain untuk tampilan
                    elif news_item['impact'] == 'Medium':
                        if display_news_impact not in ["TINGGI"]: # Menengah hanya jika tidak ada berita berdampak tinggi yang aktif untuk tampilan
                            display_news_impact = "MENENGAH"
                            display_news_impact_color = "darkorange"
                            self.log(f"🔶 BERITA BERDAMPAK MENENGAH: {news_item['headline']} pada {news_time.strftime('%H:%M:%S')}")
                    elif news_item['impact'] == 'Low':
                        if display_news_impact not in ["TINGGI", "MENENGAH"]: # Rendah hanya jika tidak ada berita berdampak tinggi/menengah yang aktif untuk tampilan
                            display_news_impact = "RENDAH"
                            display_news_impact_color = "blue"
                            self.log(f"💡 BERITA BERDAMPAK RENDAH: {news_item['headline']} pada {news_time.strftime('%H:%M:%S')}")
            
            # Perbarui label UI untuk dampak berita
            self.news_impact_label.setText(f"Dampak Saat Ini: {display_news_impact}")
            self.news_impact_label.setStyleSheet(f"font-weight: bold; color: {display_news_impact_color};")

            if next_event_time is None:
                self.next_news_label.setText("Berita Selanjutnya: N/A")
                self.next_news_label.setStyleSheet("font-weight: bold;")
            else:
                self.next_news_label.setText(next_event_details)

            self.news_status_label.setText("Status: Data berita diperbarui")
            self.news_status_label.setStyleSheet("font-weight: bold; color: darkgreen;")

        except Exception as e:
            self.log(f"Error mengambil/memproses berita: {str(e)}")
            self.news_status_label.setText("Status: Error")
            self.news_status_label.setStyleSheet("font-weight: bold; color: red;")
        finally:
            # PENTING: Setel ulang current_news_impact ke None untuk memastikan tidak memengaruhi logika trading
            current_news_impact = "None"

    def _simulate_economic_news(self):
        """
        Mensimulasikan pengambilan data berita ekonomi.
        Dalam aplikasi nyata, fungsi ini akan dihubungkan ke API berita ekonomi (misalnya, ForexFactory, Investing.com).
        """
        current_time_wib = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))
        
        simulated_news = [
            {"event": "Pernyataan FOMC", "headline": "Ketua Fed Memberikan Pidato Penting", "time": (current_time_wib + datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "impact": "High"},
            {"event": "Rilis Data CPI", "headline": "Inflasi AS Melonjak, Kekhawatiran Meningkat", "time": (current_time_wib - datetime.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"), "impact": "High"},
            {"event": "Klaim Pengangguran", "headline": "Klaim Pengangguran Mingguan Lebih Rendah dari Perkiraan", "time": (current_time_wib + datetime.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"), "impact": "Medium"},
            {"event": "PMI Manufaktur", "headline": "Sektor Manufaktur Menunjukkan Perlambatan", "time": (current_time_wib - datetime.timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S"), "impact": "Low"},
            {"event": "Pidato Gubernur Bank Sentral", "headline": "Gubernur Bank Sentral Bahas Kebijakan Moneter", "time": (current_time_wib + datetime.timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S"), "impact": "High"},
            {"event": "Data Penjualan Ritel", "headline": "Penjualan Ritel Melebihi Ekspektasi, Ekonomi Kuat", "time": (current_time_wib + datetime.timedelta(minutes=120)).strftime("%Y-%m-%d %H:%M:%S"), "impact": "Medium"},
        ]
        return simulated_news

    def run_analysis(self):
        """
        Fungsi dispatcher yang akan memicu strategi trading berdasarkan mode yang aktif.
        Juga memastikan analisis fundamental dan teknikal terus diperbarui di UI.
        """
        global current_mode
        
        if not is_running:
            return
        
        self.update_market_data()
        self.check_economic_news() # Update news info for UI

        # Perbarui label hasil trade terakhir
        self.update_last_trade_result_label()
        
        if current_mode == "AI_Long_Trade":
            self._run_ai_long_trade_strategy()
        elif current_mode == "Scalping_Bot":
            self._run_scalping_strategy()
        elif current_mode == "Sniper_Bot": # Panggil strategi sniper
            self._run_sniper_strategy()
        elif current_mode == "Monitoring":
            self.log("--- Mode Monitoring ---")
            self.log(f"Berita: Dampak Saat Ini: {self.news_impact_label.text().split(': ')[1]}")
            self.log(f"Berita: Selanjutnya: {self.next_news_label.text().split(': ')[1]}")
            self.log(f"M5: Tren {self.trend_label.text()} | RSI {self.rsi_label.text()} | OBV {self.obv_label.text()}")
            self.log(f"H1: Tren {self.higher_tf_trend_label.text()} | SNR {self.snr_label.text()} | Likuiditas {self.liquidity_label.text()}")
            self.log(f"Analisis Realtime Chart: {self.overall_analysis_label.text()}")
            self.log("-----------------------")
        else:
            self.log(f"Mode tidak dikenal: {current_mode}. Menghentikan analisis.")
            self.set_mode("Stopped")

    def _run_ai_long_trade_strategy(self):
        """
        Menganalisis pasar dan mengeksekusi trading untuk strategi AI Long Trade.
        Strategi ini menggabungkan analisis teknikal (70%) dan fundamental (30%).
        Fokus pada hold posisi untuk target profit pips yang lebih besar (30-50 pips)
        dan manajemen uang berbasis USD.
        """
        global model
        
        open_positions = mt5.positions_get(symbol=symbol)
        has_open_position = open_positions is not None and len(open_positions) > 0

        try:
            rates = mt5.copy_rates_from_pos(symbol, AI_TRADING_TIMEFRAME, 0, 100)
            rates_higher_tf = mt5.copy_rates_from_pos(symbol, AI_HIGHER_TIMEFRAME, 0, 50)
            
            if rates is None or rates_higher_tf is None:
                self.log("Gagal mendapatkan data candle untuk analisis AI Long Trade.")
                return
                
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df_higher_tf = pd.DataFrame(rates_higher_tf)
            df_higher_tf['time'] = pd.to_datetime(df_higher_tf['time'], unit='s')

            df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
            macd = MACD(df['close'])
            df['macd'] = macd.macd()
            df['macd_signal'] = macd.macd_signal()
            df['macd_hist'] = macd.macd_diff()
            df['ema20'] = EMAIndicator(df['close'], window=20).ema_indicator()
            df['ema50'] = EMAIndicator(df['close'], window=50).ema_indicator()
            bb = BollingerBands(df['close'], window=20, window_dev=2)
            df['bb_upper'] = bb.bollinger_hband()
            df['bb_lower'] = bb.bollinger_lband()
            df['bb_middle'] = bb.bollinger_mavg()
            df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
            df['atr'] = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
            if 'tick_volume' in df.columns and not df['tick_volume'].isnull().all():
                df['obv'] = OnBalanceVolumeIndicator(df['close'], df['tick_volume']).on_balance_volume()
            else: df['obv'] = 0
            df = df.dropna()

            if df.empty:
                self.log("Data candlestick M5 tidak cukup untuk analisis AI Long Trade.")
                return
            
            if not df_higher_tf.empty:
                df_higher_tf['sma20'] = SMAIndicator(df_higher_tf['close'], window=20).sma_indicator()
                df_higher_tf['sma50'] = SMAIndicator(df_higher_tf['close'], window=50).sma_indicator()
                df_higher_tf = df_higher_tf.dropna()

            higher_tf_trend = "Sideways"
            if not df_higher_tf.empty:
                last_higher_tf = df_higher_tf.iloc[-1]
                if last_higher_tf['sma20'] > last_higher_tf['sma50']: higher_tf_trend = "Up Trend"
                elif last_higher_tf['sma20'] < last_higher_tf['sma50']: higher_tf_trend = "Down Trend"
            
            latest = df.iloc[-1:]
            features = latest[['open', 'high', 'low', 'close', 'rsi', 'macd',
                               'macd_signal', 'macd_hist', 'ema20', 'ema50', 'bb_width', 'atr', 'obv']]
            
            if model is None:
                self.log("Model AI belum dilatih. Tidak dapat melakukan prediksi AI Long Trade.")
                return

            signal = model.predict(features)[0]
            proba = model.predict_proba(features)[0]
            confidence = max(proba)
            
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                self.log("Gagal mendapatkan data tick.")
                return
            current_price = tick.ask if signal == 1 else tick.bid

            self.log(f"📢 AI Long Trade: Sinyal AI {'BELI' if signal == 1 else 'JUAL'} terdeteksi. Keyakinan: {confidence:.2%}, Tren H1: {higher_tf_trend}")
            
            if has_open_position:
                for pos in open_positions:
                    target_profit_usd_value = self.trading_settings['target_profit_usd']
                    target_loss_usd_value = self.trading_settings['target_loss_usd']
                    max_hold_duration = self.trading_settings['max_hold_duration']

                    pos_open_time = datetime.datetime.fromtimestamp(pos.time)
                    time_in_position = (datetime.datetime.now() - pos_open_time).total_seconds() / 60

                    # Dihapus: Logika penutupan posisi berdasarkan berita
                    
                    if pos.profit <= -target_loss_usd_value:
                        self.log(f"🔴 AI Long Trade: Posisi #{pos.ticket} rugi mencapai target (${pos.profit:.2f}). Menutup untuk cut loss.")
                        self.close_position(pos)
                        continue
                    
                    if pos.profit >= target_profit_usd_value:
                        self.log(f"✅ AI Long Trade: Posisi #{pos.ticket} profit mencapai target (${pos.profit:.2f}). Menutup posisi.")
                        self.close_position(pos)
                        continue
                    
                    is_ai_signal_opposite = (pos.type == mt5.ORDER_TYPE_BUY and signal == 0) or \
                                             (pos.type == mt5.ORDER_TYPE_SELL and signal == 1)
                    if is_ai_signal_opposite and confidence >= 0.65 and pos.profit > 0:
                        self.log(f"🔶 AI Long Trade: Sinyal AI berbalik kuat ({confidence:.2%}). Menutup posisi #{pos.ticket} dengan profit (${pos.profit:.2f}).")
                        self.close_position(pos)
                        continue

                    if time_in_position >= max_hold_duration * 4 and pos.profit < 0:
                        self.log(f"⏰ AI Long Trade: Posisi #{pos.ticket} terlalu lama dipegang tanpa profit. Menutup.")
                        self.close_position(pos)
                        continue
                        
                    self.log(f"⚪ AI Long Trade: Posisi #{pos.ticket} sedang dipegang (${pos.profit:.2f}). Sinyal AI berlawanan: {is_ai_signal_opposite}, Conf: {confidence:.2%}")

            else: # No open positions, consider opening a new one
                equity = mt5.account_info().equity
                risk_amount_usd_per_trade = equity * (self.trading_settings['risk_percent'] / 100.0)
                
                sl_pips_for_lot_calc = self.trading_settings['sl_pips']
                calculated_lot_size = self.calculate_lot_size_by_risk(risk_amount_usd_per_trade, sl_pips_for_lot_calc, current_price)
                
                if calculated_lot_size <= 0:
                    self.log("AI Long Trade: Ukuran lot yang dihitung terlalu kecil atau tidak valid. Tidak melakukan entry.")
                    return

                symbol_info_curr = mt5.symbol_info(symbol)
                current_spread_points = (tick.ask - tick.bid) / symbol_info_curr.point
                liquidity_is_good = (current_spread_points <= self.trading_settings['max_spread'] * 0.75)

                if confidence >= 0.70 and higher_tf_trend == ("Up Trend" if signal == 1 else "Down Trend") and liquidity_is_good:
                    # Dihapus: Logika penundaan entry berdasarkan berita
                    self.log(f"✅ AI Long Trade: Melakukan entry {('BELI' if signal == 1 else 'JUAL')} dengan {calculated_lot_size:.2f} lot.")
                        
                    tp_pips_final = self.trading_settings['tp_pips']
                    sl_pips_final = self.trading_settings['sl_pips']

                    self.log(f"    TP: {tp_pips_final:.1f} pips | SL: {sl_pips_final:.1f} pips")
                        
                    self.execute_trade(signal, current_price, df,
                                       lot_size_override=calculated_lot_size,
                                       tp_pips_override=tp_pips_final,
                                       sl_pips_override=sl_pips_final)
                else:
                    self.log(f"🚫 AI Long Trade: Kondisi tidak ideal untuk entry (Conf: {confidence:.2%}, Tren H1: {higher_tf_trend}, Likuiditas: {'OK' if liquidity_is_good else 'BURUK'}).")

        except Exception as e:
            self.log(f"⚠️ Error dalam AI Long Trade: {str(e)}")

    def _run_scalping_strategy(self):
        """
        Menganalisis pasar dan mengeksekusi trading untuk strategi Scalping (M1).
        Strategi ini fokus pada RSI overbought/oversold, profit tipis ($0.30 - $2.00),
        dan manajemen risiko yang ketat.
        Tidak menunggu likuiditas dan tidak terpaku pada tren candle.
        """
        global current_news_impact
        
        open_positions = mt5.positions_get(symbol=symbol)
        has_open_position = open_positions is not None and len(open_positions) > 0

        try:
            scalping_min_profit_usd = self.trading_settings.get('target_profit_usd', 0.3)
            scalping_max_profit_usd = scalping_min_profit_usd * 4.0 if scalping_min_profit_usd * 4.0 >= 2.0 else 2.0
            scalping_max_loss_usd = self.trading_settings.get('target_loss_usd', 2.0)

            rates_m1 = mt5.copy_rates_from_pos(symbol, SCALPING_TIMEFRAME, 0, 50)
            rates_m5_for_trend = mt5.copy_rates_from_pos(symbol, SCALPING_HIGHER_TIMEFRAME, 0, 50)
            
            if rates_m1 is None or rates_m5_for_trend is None:
                self.log("Gagal mendapatkan data candle untuk analisis Scalping.")
                return
                
            df_m1 = pd.DataFrame(rates_m1)
            df_m1['time'] = pd.to_datetime(df_m1['time'], unit='s')
            df_m5_for_trend = pd.DataFrame(rates_m5_for_trend)
            df_m5_for_trend['time'] = pd.to_datetime(df_m5_for_trend['time'], unit='s')

            df_m1['rsi'] = RSIIndicator(df_m1['close'], window=14).rsi()
            df_m1['atr'] = AverageTrueRange(df_m1['high'], df_m1['low'], df_m1['close'], window=14).average_true_range()
            if 'tick_volume' in df_m1.columns and not df_m1['tick_volume'].isnull().all():
                df_m1['obv'] = OnBalanceVolumeIndicator(df_m1['close'], df_m1['tick_volume']).on_balance_volume()
            else: df_m1['obv'] = 0
            df_m1 = df_m1.dropna()

            if df_m1.empty or len(df_m1) < 2:
                self.log("Data candlestick M1 tidak cukup untuk analisis Scalping.")
                return
            
            if not df_m5_for_trend.empty:
                df_m5_for_trend['sma20'] = SMAIndicator(df_m5_for_trend['close'], window=20).sma_indicator()
                df_m5_for_trend['sma50'] = SMAIndicator(df_m5_for_trend['close'], window=50).sma_indicator()
                df_m5_for_trend = df_m5_for_trend.dropna()

            higher_tf_trend_scalping = "Sideways"
            if not df_m5_for_trend.empty:
                last_m5_candle = df_m5_for_trend.iloc[-1]
                if last_m5_candle['sma20'] > last_m5_candle['sma50']: higher_tf_trend_scalping = "Up Trend"
                elif last_m5_candle['sma20'] < last_m5_candle['sma50']: higher_tf_trend_scalping = "Down Trend"
            
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                self.log("Gagal mendapatkan data tick.")
                return
            current_price_ask = tick.ask
            current_price_bid = tick.bid

            self.log(f"⚡ Scalping: M1 Analisis, Tren M5: {higher_tf_trend_scalping}")

            if has_open_position:
                for pos in open_positions:
                    point = mt5.symbol_info(symbol).point
                    pips_gain = (current_price_bid - pos.price_open) / point / 10 if pos.type == mt5.ORDER_TYPE_BUY else (pos.price_open - current_price_ask) / point / 10
                    
                    pos_open_time = datetime.datetime.fromtimestamp(pos.time)
                    time_in_position = (datetime.datetime.now() - pos_open_time).total_seconds() / 60

                    if current_news_impact == "High":
                        self.log(f"🔴 Scalping: BERITA BERDAMPAK TINGGI terdeteksi. Menutup posisi #{pos.ticket} untuk keamanan.")
                        self.close_position(pos)
                        continue
                    
                    if pos.profit <= -scalping_max_loss_usd:
                        self.log(f"🔴 Scalping: Posisi #{pos.ticket} rugi mentok (${pos.profit:.2f}). Menutup.")
                        self.close_position(pos)
                        continue
                    
                    if pos.profit >= scalping_min_profit_usd:
                        if pos.profit >= scalping_max_profit_usd:
                            self.log(f"✅ Scalping: Posisi #{pos.ticket} profit max tercapai (${pos.profit:.2f}). Menutup.")
                            self.close_position(pos)
                            continue
                        elif pos.profit >= scalping_min_profit_usd and self._is_bearish_engulfing(df_m1) and pos.type == mt5.ORDER_TYPE_BUY:
                            self.log(f"✅ Scalping: Posisi #{pos.ticket} profit min tercapai, ada bearish engulfing. Menutup.")
                            self.close_position(pos)
                            continue
                        elif pos.profit >= scalping_min_profit_usd and self._is_bullish_engulfing(df_m1) and pos.type == mt5.ORDER_TYPE_SELL:
                            self.log(f"✅ Scalping: Posisi #{pos.ticket} profit min tercapai, ada bullish engulfing. Menutup.")
                            self.close_position(pos)
                            continue
                        
                        # Implement Trailing Stop/Break Even Plus here (if not already at BEP+)
                        # This checks if SL is still at or below original entry (for buy) or above (for sell)
                        # And if pips_gain is at least 1 pip.
                        if pips_gain >= 1 and ((pos.type == mt5.ORDER_TYPE_BUY and pos.sl < pos.price_open + (0.5 * point * 10)) or \
                                                (pos.type == mt5.ORDER_TYPE_SELL and pos.sl > pos.price_open - (0.5 * point * 10)) or pos.sl == 0.0):
                            symbol_info_current = mt5.symbol_info(symbol)
                            if symbol_info_current is not None:
                                point_value = mt5.symbol_info(symbol).point
                                be_plus_profit_pips = 0.5 # Example: move SL to +0.5 pips profit
                                if pos.type == mt5.ORDER_TYPE_BUY:
                                    new_sl_price = pos.price_open + (be_plus_profit_pips * point_value * 10)
                                else: # SELL
                                    new_sl_price = pos.price_open - (be_plus_profit_pips * point_value * 10)
                                
                                self.modify_sl_tp(pos.ticket, new_sl_price, pos.tp, "Scalping BEP+")
                            else: self.log(f"Gagal mendapatkan info simbol untuk BEP SL #{pos.ticket}.")

                    if time_in_position >= self.trading_settings['max_hold_duration']:
                        self.log(f"⏰ Scalping: Posisi #{pos.ticket} melebihi batas waktu {self.trading_settings['max_hold_duration']} menit. Menutup posisi.")
                        self.close_position(pos)
                        continue
                        
                    self.log(f"⚪ Scalping: Posisi #{pos.ticket} dipegang (${pos.profit:.2f}).")

            else: # No open positions, consider opening a new one
                last_m1_candle = df_m1.iloc[-1]
                
                last_m1_atr = df_m1['atr'].iloc[-1] if not df_m1['atr'].isnull().iloc[-1] else 0.5
                point_value = mt5.symbol_info(symbol).point
                sl_pips_dynamic = max(round(last_m1_atr * 10 / point_value), 2) # SL min 2 pips

                calculated_lot_scalping = self.calculate_lot_size_by_risk(scalping_max_loss_usd, sl_pips_dynamic, current_price_ask)
                
                target_profit_usd_scalping = np.random.uniform(scalping_min_profit_usd, scalping_max_profit_usd)
                tp_pips_dynamic = target_profit_usd_scalping / (calculated_lot_scalping * 10.0) if calculated_lot_scalping > 0 else 1.0
                tp_pips_dynamic = round(tp_pips_dynamic)

                if calculated_lot_scalping <= 0:
                    self.log("Scalping: Ukuran lot yang dihitung terlalu kecil atau tidak valid.")
                    return

                if current_news_impact in ["High", "Medium"]:
                    self.log("🚫 Scalping: Berita berdampak tinggi/menengah aktif. Menunda entry.")
                    return
                
                entry_signal = None
                rsi_val = df_m1['rsi'].iloc[-1]
                
                # Simplified RSI-based entry logic
                if rsi_val < 30 and (higher_tf_trend_scalping == "Up Trend" or higher_tf_trend_scalping == "Sideways"):
                    entry_signal = 1 # Buy on oversold in an uptrend or sideways market
                    self.log(f"💡 Scalping: RSI Oversold ({rsi_val:.2f}) dan Tren M5 {higher_tf_trend_scalping} (BUY).")
                elif rsi_val > 70 and (higher_tf_trend_scalping == "Down Trend" or higher_tf_trend_scalping == "Sideways"):
                    entry_signal = 0 # Sell on overbought in a downtrend or sideways market
                    self.log(f"💡 Scalping: RSI Overbought ({rsi_val:.2f}) dan Tren M5 {higher_tf_trend_scalping} (SELL).")

                if entry_signal is not None:
                    self.log(f"✅ Scalping: Melakukan entry {('BELI' if entry_signal == 1 else 'JUAL')} dengan {calculated_lot_scalping:.2f} lot.")
                    self.log(f"    TP: {tp_pips_dynamic:.1f} pips (${target_profit_usd_scalping:.2f}) | SL: {sl_pips_dynamic:.1f} pips (${scalping_max_loss_usd:.2f})")
                    
                    self.execute_trade(entry_signal, current_price_ask if entry_signal == 1 else current_price_bid, df_m1, # Use current_price_ask for BUY, current_price_bid for SELL
                                       lot_size_override=calculated_lot_scalping,
                                       tp_pips_override=tp_pips_dynamic,
                                       sl_pips_override=sl_pips_dynamic)
                else:
                    self.log("🚫 Scalping: Kondisi RSI/Tren tidak ideal untuk entry.")

        except Exception as e:
            self.log(f"⚠️ Error dalam Scalping: {str(e)}")

    def _is_bullish_engulfing(self, df):
        """
        Mendeteksi pola Bullish Engulfing.
        Pola pembalikan bullish yang kuat: Candle bullish besar menelan candle bearish sebelumnya.
        Args:
            df (DataFrame): DataFrame data candle.
        Returns:
            bool: True jika pola terdeteksi, False jika tidak.
        """
        if len(df) < 2: return False
        candle0 = df.iloc[-1]
        candle1 = df.iloc[-2]
        
        return (candle0['close'] > candle0['open'] and # Current candle is bullish
                candle1['close'] < candle1['open'] and # Previous candle is bearish
                candle0['close'] >= candle1['open'] and # Bullish candle engulfs previous body (top)
                candle0['open'] <= candle1['close'])    # Bullish candle engulfs previous body (bottom)

    def _is_bearish_engulfing(self, df):
        """
        Mendeteksi pola Bearish Engulfing.
        Pola pembalikan bearish yang kuat: Candle bearish besar menelan candle bullish sebelumnya.
        Args:
            df (DataFrame): DataFrame data candle.
        Returns:
            bool: True jika pola terdeteksi, False jika tidak.
        """
        if len(df) < 2: return False
        candle0 = df.iloc[-1]
        candle1 = df.iloc[-2]
        
        return (candle0['close'] < candle0['open'] and # Current candle is bearish
                candle1['close'] > candle1['open'] and # Previous candle is bullish
                candle0['close'] <= candle1['open'] and # Bearish candle engulfs previous body (bottom)
                candle0['open'] >= candle1['close'])    # Bearish candle engulfs previous body (top)

    def _is_hammer_inverted_hammer(self, df):
        """
        Mendeteksi pola Hammer atau Inverted Hammer.
        Keduanya adalah sinyal pembalikan bullish, sering muncul di akhir downtrend.
        Hammer: body kecil di atas, sumbu bawah panjang.
        Inverted Hammer: body kecil di bawah, sumbu atas panjang.
        Args:
            df (DataFrame): DataFrame data candle.
        Returns:
            bool: True jika pola terdeteksi, False jika tidak.
        """
        if len(df) < 1: return False
        candle = df.iloc[-1]
        
        body = abs(candle['close'] - candle['open'])
        lower_wick = min(candle['open'], candle['close']) - candle['low']
        upper_wick = candle['high'] - max(candle['open'], candle['close'])
        
        if df['atr'].iloc[-1] == 0 or pd.isna(df['atr'].iloc[-1]): return False
        is_small_body = body < (df['atr'].iloc[-1] * 0.3) # Body is small relative to ATR

        is_hammer = (is_small_body and
                     lower_wick > (2 * body) and # Long lower wick
                     upper_wick < body)          # Very small or no upper wick
        
        is_inverted_hammer = (is_small_body and
                              upper_wick > (2 * body) and # Long upper wick
                              lower_wick < body)           # Very small or no lower wick

        return is_hammer or is_inverted_hammer

    def _is_shooting_star_hanging_man(self, df):
        """
        Mendeteksi pola Shooting Star atau Hanging Man.
        Keduanya adalah sinyal pembalikan bearish, sering muncul di akhir uptrend/downtrend.
        Shooting Star: body kecil di bawah, sumbu atas panjang.
        Hanging Man: body kecil di atas, sumbu bawah panjang.
        Args:
            df (DataFrame): DataFrame data candle.
        Returns:
            bool: True jika pola terdeteksi, False jika tidak.
        """
        if len(df) < 1: return False
        candle = df.iloc[-1]

        body = abs(candle['close'] - candle['open'])
        lower_wick = min(candle['open'], candle['close']) - candle['low']
        upper_wick = candle['high'] - max(candle['open'], candle['close'])

        if df['atr'].iloc[-1] == 0 or pd.isna(df['atr'].iloc[-1]): return False
        is_small_body = body < (df['atr'].iloc[-1] * 0.3)

        is_shooting_star = (is_small_body and
                            upper_wick > (2 * body) and # Long upper wick
                            lower_wick < body)          # Very small or no lower wick
        
        is_hanging_man = (is_small_body and
                          lower_wick > (2 * body) and # Long lower wick
                          upper_wick < body)          # Very small or no upper wick

        return is_shooting_star or is_hanging_man

    def modify_sl_tp(self, ticket, new_sl, new_tp, comment=""):
        """
        Mengubah harga Stop Loss (SL) dan Take Profit (TP) untuk posisi yang sudah ada.
        Args:
            ticket (int): Nomor tiket posisi yang akan diubah.
            new_sl (float): Harga Stop Loss yang baru.
            new_tp (float): Harga Take Profit yang baru.
            comment (str): Komentar untuk operasi ini.
        """
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
            "magic": 123456,
            "comment": comment,
        }
        result = mt5.order_send(request)
        if result is None:
            self.log(f"❌ Modify SL/TP None: Posisi #{ticket}. Mungkin masalah koneksi.")
        elif result.retcode == mt5.TRADE_RETCODE_DONE:
            self.log(f"✅ SL/TP posisi #{ticket} berhasil diubah. SL: {new_sl:.2f}, TP: {new_tp:.2f}.")
        else:
            self.log(f"❌ Gagal mengubah SL/TP posisi #{ticket}: {self.get_error_message(result.retcode)}")


    def close_position(self, position):
        """
        Menutup posisi trading tunggal.
        Args:
            position (mt5.TradePosition): Objek posisi yang akan ditutup.
        Returns:
            bool: True jika posisi berhasil ditutup, False jika gagal.
        """
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            self.log(f"Gagal mendapatkan tick untuk menutup posisi #{position.ticket}")
            return False

        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY, # Opposite of current position type
            "position": position.ticket,
            "price": tick.bid if position.type == mt5.ORDER_TYPE_BUY else tick.ask, # Bid for closing buy, Ask for closing sell
            "deviation": 10, # Max price deviation in points
            "magic": position.magic,
            "comment": "Closed by AI Trading Bot",
            "type_time": mt5.ORDER_TIME_GTC, # Good Till Cancel
            "type_filling": mt5.ORDER_FILLING_FOK, # Fill Or Kill
        }
        
        result = mt5.order_send(close_request)
        if result is None:
            self.log(f"❌ Hasil order_send (Close Position #{position.ticket}) adalah None. Kemungkinan masalah koneksi atau server.")
            return False

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            self.log(f"✅ Posisi #{position.ticket} berhasil ditutup. Profit: ${position.profit:.2f}")
            global last_trade_result
            if position.profit > 0:
                global win_count
                win_count += 1
                last_trade_result = "Win"
            else:
                global loss_count
                loss_count += 1
                last_trade_result = "Loss"
            self.update_winrate()
            self.update_account_info()
            self.update_last_trade_result_label() # Perbarui label hasil trade
            return True
        else:
            error_msg = self.get_error_message(result.retcode)
            self.log(f"❌ Gagal menutup posisi #{position.ticket}: {error_msg}. Retcode: {result.retcode}")
            last_trade_result = "Gagal Tutup"
            self.update_last_trade_result_label() # Perbarui label hasil trade
            return False

    def get_error_message(self, retcode):
        """
        Mengembalikan pesan kesalahan yang dapat dibaca manusia dari kode pengembalian MT5.
        Args:
            retcode (int): Kode pengembalian dari operasi MT5.
        Returns:
            str: Deskripsi pesan kesalahan.
        """
        errors = {
            mt5.TRADE_RETCODE_REQUOTE: "Harga berubah (requote)",
            mt5.TRADE_RETCODE_REJECT: "Order ditolak",
            mt5.TRADE_RETCODE_CANCEL: "Order dibatalkan",
            mt5.TRADE_RETCODE_PLACED: "Order sudah diproses",
            mt5.TRADE_RETCODE_DONE: "Order berhasil",
            mt5.TRADE_RETCODE_DONE_PARTIAL: "Order parsial",
            mt5.TRADE_RETCODE_ERROR: "Error umum",
            mt5.TRADE_RETCODE_TIMEOUT: "Timeout",
            mt5.TRADE_RETCODE_INVALID: "Parameter tidak valid",
            mt5.TRADE_RETCODE_INVALID_VOLUME: "Volume tidak valid",
            mt5.TRADE_RETCODE_INVALID_PRICE: "Harga tidak valid",
            mt5.TRADE_RETCODE_INVALID_STOPS: "Stop Loss/Take Profit tidak valid",
            mt5.TRADE_RETCODE_TRADE_DISABLED: "Trading dinonaktifkan",
            mt5.TRADE_RETCODE_MARKET_CLOSED: "Market tutup",
            mt5.TRADE_RETCODE_NO_MONEY: "Dana tidak cukup",
            mt5.TRADE_RETCODE_PRICE_CHANGED: "Harga berubah",
            mt5.TRADE_RETCODE_PRICE_OFF: "Harga tidak sesuai",
            mt5.TRADE_RETCODE_INVALID_EXPIRATION: "Kadaluarsa tidak valid",
            mt5.TRADE_RETCODE_ORDER_CHANGED: "Order diubah",
            mt5.TRADE_RETCODE_TOO_MANY_REQUESTS: "Terlalu banyak permintaan",
            mt5.TRADE_RETCODE_NO_CHANGES: "Tidak ada perubahan",
            mt5.TRADE_RETCODE_SERVER_DISABLES_AT: "Auto trading dinonaktifkan di server",
            mt5.TRADE_RETCODE_CLIENT_DISABLES_AT: "Auto trading dinonaktifkan di klien",
            mt5.TRADE_RETCODE_LOCKED: "Akun terkunci",
            mt5.TRADE_RETCODE_FROZEN: "Akun beku",
            mt5.TRADE_RETCODE_INVALID_FILL: "Tipe pengisian tidak valid",
            mt5.TRADE_RETCODE_CONNECTION: "Tidak ada koneksi",
            mt5.TRADE_RETCODE_ONLY_REAL: "Hanya akun real",
            mt5.TRADE_RETCODE_LIMIT_ORDERS: "Terlalu banyak order",
            mt5.TRADE_RETCODE_LIMIT_VOLUME: "Volume melebihi batas",
            mt5.TRADE_RETCODE_INVALID_ORDER: "Order tidak valid",
            mt5.TRADE_RETCODE_POSITION_CLOSED: "Posisi sudah ditutup"
        }
        return errors.get(retcode, f"Error tidak diketahui (kode: {retcode})")

    def update_last_trade_result_label(self):
        """
        Memperbarui label hasil trade terakhir di UI.
        """
        global last_trade_result
        self.last_trade_result_label.setText(f"Hasil Trade Terakhir: {last_trade_result}")
        if last_trade_result == "Win":
            self.last_trade_result_label.setStyleSheet("font-weight: bold; color: green;")
        elif last_trade_result == "Loss" or last_trade_result == "Gagal Tutup":
            self.last_trade_result_label.setStyleSheet("font-weight: bold; color: red;")
        else:
            self.last_trade_result_label.setStyleSheet("font-weight: bold; color: black;")

    def log(self, message):
        """
        Menambahkan pesan ke area log UI dan memastikan area log menggulir ke bawah secara otomatis.
        Args:
            message (str): Pesan yang akan ditambahkan ke log.
        """
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{timestamp}] {message}")
        self.log_output.ensureCursorVisible()
        
        QApplication.processEvents() # Process UI events to update immediately

    def closeEvent(self, event):
        """
        Menangani event penutupan aplikasi.
        Memastikan timer dihentikan dan koneksi MT5 dimatikan dengan rapi.
        """
        global is_running
        if is_running:
            self.analysis_timer.stop()
            self.data_timer.stop() # Stop data timer as well
            self.news_timer.stop() # Stop news timer
            is_running = False
            
        mt5.shutdown()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    bot = TradingBotGUI()
    bot.show()
    sys.exit(app.exec())
