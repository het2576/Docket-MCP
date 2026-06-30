const http = require('http');

const data = JSON.stringify({
  transcript: "Priya: Het, can you fix the database this week?",
  source_meeting: "Local smoke test"
});

const options = {
  hostname: '127.0.0.1',
  port: 3000,
  path: '/api/process',
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Content-Length': data.length
  }
};

const req = http.request(options, (res) => {
  let chunks = '';
  res.on('data', d => chunks += d);
  res.on('end', () => console.log('STATUS:', res.statusCode, '\nBODY:', chunks));
});
req.on('error', error => console.error(error));
req.write(data);
req.end();
