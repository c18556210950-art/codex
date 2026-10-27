// Extract the actual checked-in integrated code without starting its HTTP server.
const fs=require('fs'),path=require('path');
const server=fs.readFileSync(path.join(__dirname,'../src/server/server.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../src/server/controller.html'),'utf8');
function between(text,start,end){const a=text.indexOf(start),b=text.indexOf(end,a+start.length);if(a<0||b<0)throw Error('Reference section boundaries changed: '+start);return text.slice(a,b)}
exports.autonomy=between(server,'// The PC supplies perception;','let navigation={');
exports.navigation=between(server,'let navigation={',"server.on('upgrade'");
exports.phoneSensors=between(html,'let phoneGeoWatch=', 'let visionSocket=');
exports.navigationBrowser=between(html,'let navMap=', 'updateButtons();refresh();setInterval(sendDrive,200);');
