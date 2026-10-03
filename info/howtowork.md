*Tütün Sabri* bazı işleri senin yerine takip eder, haber verir ve gerektiğinde müdahale etmeni ister.

*Nasıl çalışır?*

- Bot üzerinden bir komut verirsin.
- Uygun yetkin varsa ilgili işlem arka planda başlatılır.
- Süreç boyunca önemli değişikliklerde sana mesaj gelir.
- İşlem iptal edilebilir, bazı işlemlerde tutulan kaynaklar manuel olarak bırakılabilir.

*Günlük haber özeti:*

- Her gün Türkiye saatiyle 09.00 ve 19.00'da otomatik gönderilir.
- Gündem, öne çıkan konular, teknoloji ve yazılım haberleri internetten toplanır.
- Yerel Ollama modeli haberleri kısa ve anlaşılır bir Türkçe bültene dönüştürür.
- Bülten aktif yönetici hesabına gönderilir.
- İstediğin zaman `/news` yazarak kendin için anlık bir haber özeti hazırlatabilirsin.
- `/podcast` komutu güncel haberlerden tamamen yerelde bir MP3 podcast hazırlar.
- Podcast üretimi Ollama, Türkçe `espeak-ng` ve `ffmpeg` kullandığı için birkaç dakika sürebilir.

*Yerel AI asistanı:*

- `/ai SORUNUZ` yazarak yerel Ollama modeline soru sorabilirsin.
- Örnek: `/ai Python'da async await nasıl çalışır?`
- Yanıt sunucudaki yerel model tarafından üretilir.

*YHT tarafında mantık şudur:*

- Kalkış, varış, tarih ve saat bilgisi alınır.
- Bot arka planda seferi düzenli aralıklarla kontrol eder.
- Ekonomi ve business doluluk değişimleri izlenir.
- Uygun koltuk bulunduğunda seni hemen bilgilendirir.
- Koltuk tutulursa belirli bir süre boyunca sende kalır; gerekirse manuel bırakabilirsin.

*Yetki konusu:*

- `/start` komutu herkese açıktır.
- Diğer komutlar için botta kayıtlı ve aktif kullanıcı olman gerekir.
- Yetkin yoksa doğrudan bot üzerinden yetki talebi gönderebilirsin.
