FROM apify/actor-node-playwright-chrome:latest

USER root

RUN apt-get update \
    && apt-get install -y python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip3 install --no-cache-dir \
    --break-system-packages \
    -r requirements.txt

COPY package*.json ./

RUN npm --quiet set progress=false \
    && npm install --omit=dev --omit=optional

COPY . ./

CMD npm start