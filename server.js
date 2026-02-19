const express = require('express');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;

// Обслуживаем статические файлы
app.use(express.static(path.join(__dirname)));

// Для всех остальных маршрутов отдаём index.html
app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, () => {
    console.log(`🌐 NeoChat HTTP Server running on port ${PORT}`);
});
